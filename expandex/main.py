import re
import os
import cv2
import PIL
import sys
import json
import time
import imghdr
import hashlib
import requests
import cloudscraper
import numpy as np
from antidupe import Antidupe
from featurecrop import featurecrop
from pathlib import Path
from threading import Thread
from PIL import Image
from io import BytesIO
from playwright.sync_api import sync_playwright
from playwright._impl._errors import (  # noqa
    Error,
    TimeoutError
)

test_image = Path('./bug.jpg')

image_extensions = [
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".tiff",
    ".svg",
]

DEFAULTS = {
    'ih': 0.1,  # 8x8x1 Image Hash similarity.
    'ssim': 0.15,  # Structural similarity index measurement.
    'cs': 0.1,  # NumPy cosine similarity.
    'cnn': 0.15,  # EfficientNet feature extraction.
    'dedup': 0.1  # Mobilenet cosine similarities.
}


class Locator:
    """
    Online image search using Yandex image lookup.
    """
    term = False
    retries = dict()
    search_url = 'https://yandex.com/images/search'
    context = None
    returns = 0
    depth = 0
    size = (0, 0)
    mat = None
    threads = 0

    selectors = {
        'similar_image_button': '[id^="CbirNavigation-"] > nav > div > div > div > div > '
                                'a.CbirNavigation-TabsItem.CbirNavigation-TabsItem_name_similar-page',
        'big_image_preview': 'body > div.Modal.Modal_visible.Modal_theme_normal.ImagesViewer-Modal.ImagesViewer > '
                             'div.Modal-Wrapper > div > div > div > div.ImagesViewer-Layout.ImagesViewer-Container > '
                             'div > div.ImagesViewer-TopSide > div.ImagesViewer-LayoutMain > '
                             'div.ImagesViewer-LayoutScene > div.ImagesViewer-View > div > img',
        'resolution_dropdown': 'body > div.Modal.Modal_visible.Modal_theme_normal.ImagesViewer-Modal.ImagesViewer > '
                               'div.Modal-Wrapper > div > div > div > div.ImagesViewer-Layout.ImagesViewer-Container '
                               '> div > div.ImagesViewer-TopSide > div.ImagesViewer-LayoutSideblock > div > div > div '
                               '> div.MMViewerButtons > '
                               'div.OpenImageButton.OpenImageButton_text.OpenImageButton_sizes.MMViewerButtons'
                               '-OpenImageSizes > button',
        'resolution_links': 'body > div.Modal.Modal_visible.Modal_theme_normal.ImagesViewer-Modal.ImagesViewer > '
                            'div.Modal-Wrapper > div > div > div > div.ImagesViewer-Layout.ImagesViewer-Container > '
                            'div > div.ImagesViewer-TopSide > div.ImagesViewer-LayoutSideblock > div > div > div > '
                            'div.MMViewerButtons > '
                            'div.OpenImageButton.OpenImageButton_text.OpenImageButton_sizes.MMViewerButtons'
                            '-OpenImageSizes > div > ul',
        'open_button': 'body > div.Modal.Modal_visible.Modal_theme_normal.ImagesViewer-Modal.ImagesViewer > '
                       'div.Modal-Wrapper > div > div > div > div.ImagesViewer-Layout.ImagesViewer-Container > div > '
                       'div.ImagesViewer-TopSide > div.ImagesViewer-LayoutSideblock > div > div > div > '
                       'div.MMViewerButtons > div.OpenImageButton.OpenImageButton_text.MMViewerButtons-OpenImageSizes '
                       '> a',
    }

    def __init__(self, save_folder: str = '', deduplicate: str = 'cpu', weights: dict = DEFAULTS, debug: bool = False):  # noqa
        self.debug = debug
        self.save_folder = save_folder
        self.deduplicate = deduplicate
        if self.deduplicate:
            self.deduplicator = Antidupe(
                device=self.deduplicate,
                limits=weights,
                debug=self.debug
            )

    def _deduplicate(self, image: np.ndarray) -> bool:
        """
        Checks to see if the image is a duplicate of one of the ones in the save folder.
        """
        result = False
        images = os.listdir(self.save_folder)
        if self.deduplicator.predict([image.copy(), self.mat.copy()]):  # Check original.
            result = True
        else:
            for image_file in images:  # Check downloaded images.
                if self.term:
                    break
                image_file_path = os.path.join(self.save_folder, image_file)
                if os.path.isdir(image_file_path):
                    continue
                if not imghdr.what(image_file_path):
                    continue
                try:
                    im = Image.open(image_file_path)
                except (IOError, OSError):
                    continue
                dupe = self.deduplicator.predict([image.copy(), im])
                if dupe:
                    result = True
                    break
        return result

    def d_print(self, *args, **kwargs):
        """
        Debug messanger.
        """
        if self.debug:
            print(*args, **kwargs)

    def _set_save_folder(self, location: str) -> None:
        """
        Configure our save directory
        """
        if not self.save_folder:
            self.save_folder = f"{location}_images"
        os.makedirs(self.save_folder, exist_ok=True)

    def _get_image_from_anything(self, image: [Path, np.ndarray, Image.Image, str]) -> Image.Image:
        """
        Collect an image from a file, mat, array, or url.
        """
        def get_name_from_path(path: Path) -> str:
            """
            Collects a filename from a Path object.
            """
            name = f"{path.stem}{path.suffix}"
            return name.lower()

        def is_url_or_filepath(input_string):
            url_pattern = r'^https?://.+'
            filepath_pattern = r'^(/|([a-zA-Z]:)?\\).+'
            if re.match(url_pattern, input_string):
                return "url"
            elif re.match(filepath_pattern, input_string):
                return "file"
            else:
                return None

        if isinstance(image, str):  # Path string.
            op = is_url_or_filepath(image)
            if op == 'file':
                image = Path(image)

        if isinstance(image, np.ndarray):  # Numpy array.
            result = Image.fromarray(image)
            self._set_save_folder('np_array')
        elif isinstance(image, Image.Image):  # PIL image.
            result = image
            self._set_save_folder('pil_image')
        elif isinstance(image, Path):  # Pathlib path object.
            result = image.expanduser().resolve()
            if not result.is_file():
                raise FileNotFoundError()
            self._set_save_folder(get_name_from_path(result))
            result = Image.open(result.as_posix())
        elif isinstance(image, str):  # URL string.
            file_name = self.extract_filename_from_url(image)
            if not file_name:
                file_name = 'url'
            file_name = file_name.lower()
            self._set_save_folder(file_name)
            original_setting = bool(self.deduplicate)
            self.deduplicate, self.returns = False, -1
            self.download_image(image)
            self.deduplicate, self.returns = original_setting, 0
            result = Image.open(f"{self.save_folder}/{file_name}")
        else:
            raise TypeError(f'unable to locate image from {type(image)}')
        return result

    def test_get_anything(self):
        """
        Just a quick test of the method above.
        """
        mat = Image.open('bug.jpg')
        images = [
            'https://upload.wikimedia.org/wikipedia/commons/d/d5/Bug.jpg',
            'bug.jpg',
            Path('bug.jpg'),
            mat,
            np.array(mat)
        ]
        for idx, image in enumerate(images):
            print(idx, self.save_folder)
            result = self._get_image_from_anything(image)
            print(type(result))
            self.save_folder = ''
        self.save_folder = './test_images'
        for idx, image in enumerate(images):
            result = self._get_image_from_anything(image)
            print(idx, type(result), self.save_folder)

    @staticmethod
    def generate_md5(content):
        md5 = hashlib.md5()
        md5.update(content)
        return md5.hexdigest()

    def get_image_format(self, image_data):
        try:
            image = Image.open(BytesIO(image_data))
            self.d_print(image.format)
            return str(image.format).lower()
        except Exception as e:
            self.d_print("Error:", e)
            return None

    def extract_filename_from_url(self, url: str) -> [str, None]:
        """
        Extract file names from URLs
        """
        matches = re.findall(r'[^/\\]*\.\w+', url)
        if matches:
            for match in matches:
                if self.term:
                    break
                self.d_print(match)
                for ext in image_extensions:
                    if match.endswith(ext):
                        return match.lower()
        else:
            return None

    @staticmethod
    def find_selector(selector: str, page: any) -> bool:
        """
        locates the presence of a selector.
        """
        answer = page.query_selector(selector)
        return answer is not None

    def init_web(self, destination_url: str, callback: any, *args, **kwargs) -> any:
        """
        This will create our web contexts allowing us to interact with the remote data.
        """
        scraper = cloudscraper.create_scraper()
        try:
            with sync_playwright() as p:
                browser = p.firefox.launch()
                self.context = browser.new_context()
                url = destination_url
                response = scraper.get(url)
                cookies = list()
                for c in response.cookies:
                    name, value, domain = c.name, c.value, c.domain
                    cookie = {"name": name, "value": value, "domain": domain, 'path': '/'}
                    self.d_print(cookie)
                    cookies.append(cookie)
                self.context.add_cookies(cookies)
                page = self.context.new_page()
                page.goto(url)
                kwargs['page'] = page
                result = callback(*args, **kwargs)
                page.close()
                self.context.close()
                browser.close()
        except KeyboardInterrupt:
            try:
                self.context.close()
                browser.close()
            except Error:
                pass
            finally:
                sys.exit()
        finally:
            scraper.close()
        return result

    def get_search_root(self, image: Image.Image) -> str:
        """
        Uploads an image to pasteboard and returns its URL.

        NOTE: Image_path must be a full path to a local image file **not** relative.
        """
        self.mat = image
        content_type = 'image/jpeg'
        image_bytes = BytesIO()
        image.save(image_bytes, format='JPEG')
        image_bytes.seek(0)
        files = {'upfile': ('blob', image_bytes, content_type)}
        params = {'rpt': 'imageview', 'format': 'json',
                  'request': '{"blocks":[{"block":"b-page_type_search-by-image__link"}]}'}
        response = requests.post(self.search_url, params=params, files=files)
        query_string = json.loads(response.content)['blocks'][0]['params']['url']
        img_search_url = self.search_url + '?' + query_string
        return img_search_url

    def test_upload_image(self) -> str:
        """
        Simple test of the logic above.
        """
        image = self._get_image_from_anything(test_image)
        img_search_url = self.get_search_root(image)
        self.d_print(img_search_url)
        return img_search_url

    def get_image_link(self, page: any, link: any) -> [str, None]:
        """
        This will evaluate the available image size links, and choose the best one.
        """
        self.d_print(link)
        page.goto(link)
        highest_resolution_url = None
        try:
            page.wait_for_load_state("networkidle")
            if self.find_selector(self.selectors['resolution_dropdown'], page):
                page.click(self.selectors['resolution_dropdown'])
                self.d_print('resolution links found')
                resolution_dropdown = page.query_selector(self.selectors['resolution_links'])
                resolution_links = resolution_dropdown.query_selector_all("li a")
                highest_resolution = 0
                highest_resolution_url = ""
                for _link in resolution_links:
                    resolution_text = _link.text_content()
                    resolution = tuple(map(int, resolution_text.split('×')))
                    if resolution[0] * resolution[1] > highest_resolution:
                        highest_resolution = resolution[0] * resolution[1]
                        highest_resolution_url = _link.get_attribute("href")
                pass
            else:
                open_selection = page.query_selector(self.selectors['open_button'])
                if open_selection:
                    self.d_print('found open')
                    highest_resolution_url = open_selection.get_attribute("href")
                else:
                    self.d_print('no resolution options were found')
                pass
            del self.retries[link]
            return highest_resolution_url
        except TimeoutError:
            self.retries[link] += 1
            if self.retries[link] < 4:
                self.d_print('max retries reached, aborting')
                del self.retries[link]
                return None
            self.d_print(f'retrying download {link}')
            self.get_image_link(page, link)

    def download_image(self, image_url: str):
        """
        Aptly named.
        """
        self.threads += 1
        if '127.0.0.1' not in image_url:
            name = self.extract_filename_from_url(image_url)
            if name is None:
                filename = 'hidden'
            else:
                filename = name
                if os.path.exists(os.path.join(self.save_folder, filename)):
                    self.d_print(f"Skipping {filename}. File already exists.")
                    self.returns += 1
                    self.threads -= 1
                    return None
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(image_url, headers=headers)
            if response.status_code == 200:
                if name is None:
                    image_format = self.get_image_format(response.content)
                    if image_format is None:
                        self.d_print(f'skipping bad image: {image_url}')
                        self.threads -= 1
                        return None
                    filename = f"{self.generate_md5(response.content)}.{image_format}"
                    pass
                bad = False
                image = Image.open(BytesIO(response.content))
                image = featurecrop(np.array(image))
                if self.deduplicate:
                    try:
                        image_array = np.array(image)
                        bad = self._deduplicate(image_array)
                        if bad:
                            self.d_print(f'skipping duplicate image: {image_url}')
                    except PIL.UnidentifiedImageError:
                        self.d_print(f"Skipping unreadable image: {image_url}")
                        bad = True
                if not bad and self.returns < self.depth:
                    self.returns += 1
                    cv2.imwrite(os.path.join(self.save_folder, filename), cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                    self.d_print(f"Downloaded {filename}")
                    self.d_print('\nreturns\n', self.returns)

            else:
                self.d_print(f"Failed to download {image_url}. Status code: {response.status_code}")
        else:
            self.d_print(f"skipping localhost redirect: {image_url}")
        self.threads -= 1
        return None

    def get_similar_images(self, page: any, depth: int = 4) -> list:
        """
        This will locate similar images and return up to the number specified in the `depth` argument.
        """
        self.returns = 0
        self.depth = depth
        self.term = False
        result = list()
        image_links = list()
        button = self.selectors['similar_image_button']
        page.wait_for_selector(button)
        page.click(button)
        page.wait_for_load_state('networkidle')
        elements = page.query_selector_all('div a')
        for element in elements:
            link = element.get_attribute("href")
            if link:
                if '/images/search?' in link:
                    url = f"{self.search_url}{link.replace('/images/search', '')}"
                    image_links.append(url)
        for image_link in image_links:
            if self.term:
                break
            self.retries[image_link] = 0
            link = self.get_image_link(page, image_link)
            if link is not None:
                result.append(link)
                if self.returns >= depth:
                    self.d_print('successfully located the requested image depth, operation complete')
                    self.term = True
                    break
                while self.threads >= (self.depth - self.returns):
                    if self.term or self.depth - self.returns == 0:
                        return result
                    time.sleep(0.1)
                if not self.term:
                    thread = Thread(target=self.download_image, args=(link, ), daemon=True)
                    thread.start()
        return result

    def test_similar_images(self):
        """
        Test get_similar_images() method.
        """
        kwargs = {
            'depth': 10
        }
        search_url = self.test_upload_image()
        result = self.init_web(
            destination_url=search_url,
            callback=self.get_similar_images,
            **kwargs
        )
        return result

    def scout(self, image: [Path, np.ndarray, Image.Image], depth: int = 10):
        """
        Send a path, or image mat and discover similar images.

        Gather to the number of images specified in the depth argument.
        """
        kwargs = {
            'depth': depth
        }
        image = self._get_image_from_anything(image)
        search_url = self.get_search_root(image)
        result = self.init_web(
            destination_url=search_url,
            callback=self.get_similar_images,
            **kwargs
        )
        return result

    def test_scout(self):
        """
        Tests the method above.
        """
        self.scout(test_image)
