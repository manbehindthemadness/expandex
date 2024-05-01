import re
import os
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
    'ih': 0.1,
    'ssim': 0.15,
    'cs': 0.1,
    'cnn': 0.15,
    'dedup': 0.1
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

    @staticmethod
    def _autocrop(image: Image.Image) -> Image.Image:
        """
        https://stackoverflow.com/questions/14211340/automatically-cropping-an-image-with-python-pil
        """
        if image.mode != 'RGB':
            image = image.convert('RGB')

        image_data = np.asarray(image)
        image_data_bw = image_data.max(axis=2)
        non_empty_columns = np.where(image_data_bw.max(axis=0) > 0)[0]
        non_empty_rows = np.where(image_data_bw.max(axis=1) > 0)[0]
        cropBox = (min(non_empty_rows), max(non_empty_rows), min(non_empty_columns), max(non_empty_columns))

        image_data_new = image_data[cropBox[0]:cropBox[1] + 1, cropBox[2]:cropBox[3] + 1, :]

        return Image.fromarray(image_data_new)

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

    def extract_filename_from_url(self, url: str):
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
                        return match
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

    def get_search_root(self, image_path: Path) -> str:
        """
        Uploads an image to pasteboard and returns its URL.

        NOTE: Image_path must be a full path to a local image file **not** relative.
        """
        if not image_path.is_file():
            raise FileNotFoundError

        image = Image.open(image_path)
        self.mat = image

        # Determine image format and set appropriate content type
        image_format = image.format.lower()
        if image_format == "jpeg":
            content_type = "image/jpeg"
        elif image_format == "png":
            content_type = "image/png"
        elif image_format == "gif":
            content_type = "image/gif"
        else:
            raise ValueError("Unsupported image format")

        # Get image size
        size = image.size
        channels = 3  # Assuming RGB image
        if len(image.getbands()) == 4:
            channels = 4  # Alpha channel present

        # Set self.size variable
        self.size = (*size, channels)

        image_path = image_path.resolve().as_posix()
        if not self.save_folder:
            self.save_folder = f"{image_path}_images"
        os.makedirs(self.save_folder, exist_ok=True)
        file_path = image_path
        search_url = self.search_url
        files = {'upfile': ('blob', open(file_path, 'rb'), content_type)}
        params = {'rpt': 'imageview', 'format': 'json',
                  'request': '{"blocks":[{"block":"b-page_type_search-by-image__link"}]}'}
        response = requests.post(search_url, params=params, files=files)
        query_string = json.loads(response.content)['blocks'][0]['params']['url']
        img_search_url = search_url + '?' + query_string
        return img_search_url

    def test_upload_image(self) -> str:
        """
        Simple test of the logic above.
        """
        image_path = test_image
        img_search_url = self.get_search_root(image_path)
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
                if self.deduplicate:
                    try:
                        image = Image.open(BytesIO(response.content))
                        image = self._autocrop(image)
                        image_array = np.array(image)
                        bad = self._deduplicate(image_array)
                        if bad:
                            self.d_print(f'skipping duplicate image: {image_url}')
                    except PIL.UnidentifiedImageError:
                        self.d_print(f"Skipping unreadable image: {image_url}")
                        bad = True
                if not bad and self.returns < self.depth:
                    self.returns += 1
                    with open(os.path.join(self.save_folder, filename), 'wb') as f:
                        f.write(response.content)
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
