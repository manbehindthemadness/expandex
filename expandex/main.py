import re
import os
import sys
import json
import hashlib
import requests
import cloudscraper
from pathlib import Path
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


class Locator:
    """
    Online image search using Yandex image lookup.
    """
    term = False
    retries = 0
    search_url = 'https://yandex.com/images/search'

    selectors = {
        'similar_image_button': '[id^="CbirNavigation-"] > nav > div > div > div > div > a.CbirNavigation-TabsItem.CbirNavigation-TabsItem_name_similar-page',
        'big_image_preview': 'body > div.Modal.Modal_visible.Modal_theme_normal.ImagesViewer-Modal.ImagesViewer > div.Modal-Wrapper > div > div > div > div.ImagesViewer-Layout.ImagesViewer-Container > div > div.ImagesViewer-TopSide > div.ImagesViewer-LayoutMain > div.ImagesViewer-LayoutScene > div.ImagesViewer-View > div > img',
        'resolution_dropdown': 'body > div.Modal.Modal_visible.Modal_theme_normal.ImagesViewer-Modal.ImagesViewer > div.Modal-Wrapper > div > div > div > div.ImagesViewer-Layout.ImagesViewer-Container > div > div.ImagesViewer-TopSide > div.ImagesViewer-LayoutSideblock > div > div > div > div.MMViewerButtons > div.OpenImageButton.OpenImageButton_text.OpenImageButton_sizes.MMViewerButtons-OpenImageSizes > button',
        'resolution_links': 'body > div.Modal.Modal_visible.Modal_theme_normal.ImagesViewer-Modal.ImagesViewer > div.Modal-Wrapper > div > div > div > div.ImagesViewer-Layout.ImagesViewer-Container > div > div.ImagesViewer-TopSide > div.ImagesViewer-LayoutSideblock > div > div > div > div.MMViewerButtons > div.OpenImageButton.OpenImageButton_text.OpenImageButton_sizes.MMViewerButtons-OpenImageSizes > div > ul',
        'open_button': 'body > div.Modal.Modal_visible.Modal_theme_normal.ImagesViewer-Modal.ImagesViewer > div.Modal-Wrapper > div > div > div > div.ImagesViewer-Layout.ImagesViewer-Container > div > div.ImagesViewer-TopSide > div.ImagesViewer-LayoutSideblock > div > div > div > div.MMViewerButtons > div.OpenImageButton.OpenImageButton_text.MMViewerButtons-OpenImageSizes > a',
    }

    def __init__(self, save_folder: str = '', debug: bool = False):
        self.debug = debug
        self.save_folder = save_folder

    @staticmethod
    def generate_md5(content):
        md5 = hashlib.md5()
        md5.update(content)
        return md5.hexdigest()

    @staticmethod
    def get_image_format(image_data):
        try:
            image = Image.open(BytesIO(image_data))
            print(image.format)
            return str(image.format).lower()
        except Exception as e:
            print("Error:", e)
            return None

    @staticmethod
    def extract_filename_from_url(url: str):
        """
        Extract file names from URLs
        """
        matches = re.findall(r'[^/\\]*\.\w+', url)
        if matches:
            for match in matches:
                print(match)
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

    @staticmethod
    def init_web(destination_url: str, callback: any, *args, **kwargs) -> any:
        """
        This will create our web contexts allowing us to interact with the remote data.
        """
        scraper = cloudscraper.create_scraper()
        try:
            with sync_playwright() as p:
                browser = p.firefox.launch()
                context = browser.new_context()
                url = destination_url
                response = scraper.get(url)
                cookies = list()
                for c in response.cookies:
                    name, value, domain = c.name, c.value, c.domain
                    cookie = {"name": name, "value": value, "domain": domain, 'path': '/'}
                    print(cookie)
                    cookies.append(cookie)
                context.add_cookies(cookies)
                page = context.new_page()
                page.goto(url)
                kwargs['page'] = page
                result = callback(*args, **kwargs)
                page.close()
                context.close()
                browser.close()
        except KeyboardInterrupt:
            try:
                context.close()
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
        Uploads an image to pasteboard and returns it's url.

        NOTE: Image_path must be a full path to a local image file **not** relative.
        """
        if not image_path.is_file():
            raise FileNotFoundError
        image_path = image_path.resolve().as_posix()
        if not self.save_folder:
            self.save_folder = f"{image_path}_images"
        file_path = image_path
        search_url = self.search_url
        files = {'upfile': ('blob', open(file_path, 'rb'), 'image/jpeg')}
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
        print(img_search_url)
        return img_search_url

    def get_image_link(self, page: any, link: any) -> [str, None]:
        """
        This will evaluate the available image size links, and choose the best one.
        """
        print(link)
        page.goto(link)
        highest_resolution_url = None
        try:
            page.wait_for_load_state("networkidle")
            if self.find_selector(self.selectors['resolution_dropdown'], page):
                page.click(self.selectors['resolution_dropdown'])
                print('resolution links found')
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
                    print('found open')
                    highest_resolution_url = open_selection.get_attribute("href")
                else:
                    print('no resolution options were found')
                pass
            return highest_resolution_url
        except TimeoutError:
            self.retries += 1
            if self.retries < 4:
                print('max retries reached, aborting')
                return None
            print(f'retrying download {link}')
            self.get_image_link(page, link)

    def download_images(self, image_urls: list, page: any):
        """
        Aptly named.
        """
        folder_path = self.save_folder
        os.makedirs(folder_path, exist_ok=True)

        for url in image_urls:
            name = self.extract_filename_from_url(url)
            if name is None:
                filename = 'hidden'
            else:
                filename = name
                if os.path.exists(os.path.join(folder_path, filename)):
                    print(f"Skipping {filename}. File already exists.")
                    continue
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                if name is None:
                    image_format = self.get_image_format(response.content)
                    if image_format is None:
                        print(f'skipping bad image: {url}')
                        continue
                    filename = f"{self.generate_md5(response.content)}.{image_format}"
                    pass
                with open(os.path.join(folder_path, filename), 'wb') as f:
                    f.write(response.content)
                print(f"Downloaded {filename}")
            else:
                print(f"Failed to download {url}. Status code: {response.status_code}")

    def get_similar_images(self, page: any, depth: int = 4, download: bool = True) -> list:
        """
        This will locate similar images and return up to the number specified in the `depth` argument.
        """
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
            self.retries = 0
            link = self.get_image_link(page, image_link)
            if link is not None:
                result.append(link)
                if len(result) >= depth:
                    break
        if download:
            self.download_images(result, page)
        return result

    def test_similar_images(self):
        """
        Test get_similar_images() method.
        """
        kwargs = {
            'depth': 20
        }
        search_url = self.test_upload_image()
        result = self.init_web(
            destination_url=search_url,
            callback=self.get_similar_images,
            **kwargs
        )
        return result
