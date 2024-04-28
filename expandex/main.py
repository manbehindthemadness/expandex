import sys
import time
import json
import requests
import cloudscraper
from tqdm import tqdm
from pathlib import Path
from random import shuffle
from playwright.sync_api import sync_playwright
from playwright._impl._errors import (  # noqa
    Error,
    TimeoutError
)


class Locator:
    """
    Online image search using Yandex image lookup.
    """
    term = False

    search_url = 'https://yandex.com/images/search'

    search_selectors = {
        'upload_button': 'body > div.header-container > header > div > div.serp-header__under > '
                         'div.serp-header__search2 > form > div.search2__input > span > span > button',
        'image_url_field': '',
        'similar_image_button': '#CbirNavigation-OybFYFY > nav > div > div > div > div > '
                                'a.CbirNavigation-TabsItem.CbirNavigation-TabsItem_name_similar-page',
    }

    def __init__(self, debug: bool = False):
        self.debug = debug

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
                context.add_cookies([{"name": c.name, "value": c.value, "domain": c.domain} for c in response.cookies])
                page = context.new_page()
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
        file_path = image_path
        search_url = self.search_url
        files = {'upfile': ('blob', open(file_path, 'rb'), 'image/jpeg')}
        params = {'rpt': 'imageview', 'format': 'json',
                  'request': '{"blocks":[{"block":"b-page_type_search-by-image__link"}]}'}
        response = requests.post(search_url, params=params, files=files)
        query_string = json.loads(response.content)['blocks'][0]['params']['url']
        img_search_url = search_url + '?' + query_string
        return img_search_url

    def test_upload_image(self):
        """
        Simple test of the logic above.
        """
        image_path = Path('./bug.jpg')
        img_search_url = self.get_search_root(image_path)
        print(img_search_url)
