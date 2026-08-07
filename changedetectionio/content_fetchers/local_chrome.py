"""html_local_chrome fetcher (spec 9).

Connects to the LocalChromeManager's loopback CDP endpoint, reuses Chrome's
persistent default context (never browser.new_context()), opens one tab per
task, and reuses the existing stateless capabilities (browser steps, screenshot,
xpath, instock, favicon) WITHOUT modifying the old Playwright fetcher.
"""
import json
import os

from loguru import logger

from changedetectionio.content_fetchers import (
    SCREENSHOT_MAX_HEIGHT_DEFAULT, visualselector_xpath_selectors,
    XPATH_ELEMENT_JS, INSTOCK_DATA_JS, FAVICON_FETCHER_JS,
)
from changedetectionio.content_fetchers.base import Fetcher
from changedetectionio.content_fetchers.exceptions import (
    EmptyReply, Non200ErrorCodeReceived, PageUnloadable, LocalChromeAttentionRequired,
)
from changedetectionio.content_fetchers.playwright import capture_full_page_async
from changedetectionio.local_browser import is_local_chrome_supported, is_local_chrome_enabled
from changedetectionio.local_browser.manager import get_manager, LocalChromeUnavailable
from changedetectionio.local_browser.task_gate import get_gate
from changedetectionio.local_browser.auth_detector import detect_auth_challenge


class fetcher(Fetcher):
    fetcher_description = "Local Chrome - Persistent Profile"

    supports_browser_steps = True
    supports_screenshots = True
    supports_xpath_element_data = True
    # Hidden from available_fetchers() default list; views add it to the choices
    # only when Windows + enabled (spec 12.3).
    selectable_in_ui = False

    def __init__(self, proxy_override=None, custom_browser_connection_url=None, **kwargs):
        super().__init__(**kwargs)
        # Proxy override is intentionally ignored: Local Chrome uses the user's
        # persistent profile/network; we never inject a proxy (spec 14).
        self._datastore = None

    def _check_available(self):
        if not is_local_chrome_supported():
            raise LocalChromeUnavailable("Local Chrome is only supported on Windows in Phase 1.")
        # The worker sets _datastore in call_browser() so the enabled flag is checked.
        if self._datastore is not None and not is_local_chrome_enabled(self._datastore):
            raise LocalChromeUnavailable(
                "Local Chrome is disabled in Settings. Enable it or choose another fetcher."
            )

    async def run(self,
                  fetch_favicon=True,
                  current_include_filters=None,
                  empty_pages_are_a_change=False,
                  ignore_status_codes=False,
                  is_binary=False,
                  request_body=None,
                  request_headers=None,
                  request_method=None,
                  screenshot_format=None,
                  timeout=None,
                  url=None,
                  watch_uuid=None):
        self._check_available()
        self.watch_uuid = watch_uuid

        manager = get_manager()
        gate = get_gate()

        chrome_path = None
        if self._datastore is not None:
            lc = self._datastore.data['settings']['requests'].get('local_chrome', {})
            chrome_path = lc.get('chrome_executable')
        chrome_path = manager.find_chrome_executable(custom_path=chrome_path)
        manager.ensure_running(chrome_path=chrome_path)

        # Phase 1: the persistent profile owns cookies/UA, so per-watch
        # request_headers/method/body/is_binary are intentionally not applied
        # (the proxy_override is also ignored - see __init__).
        async with gate.acquire(watch_uuid):
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(manager.cdp_endpoint(), timeout=60000)
                # Reuse the persistent default context; never new_context() (spec 9.1).
                if not browser.contexts:
                    raise LocalChromeUnavailable(
                        "Chrome has no persistent browser context; the dedicated profile may not be in use."
                    )
                context = browser.contexts[0]
                page = await context.new_page()
                # Phase 1: the attention tab is kept open in Chrome (see the
                # `attention` flag below). Target-id-based tab recovery
                # (reconnecting to the kept tab on recheck) is deferred.

                attention = False
                try:
                    try:
                        response = await page.goto(url, wait_until="domcontentloaded", timeout=(timeout or 45) * 1000)
                    except Exception as e:
                        raise PageUnloadable(url=url, status_code=None, message=str(e))
                    if response is None:
                        raise EmptyReply(url=url, status_code=None)

                    try:
                        self.headers = await response.all_headers()
                    except TypeError:
                        self.headers = response.all_headers()
                    self.status_code = response.status

                    if self.webdriver_js_execute_code:
                        await page.evaluate(self.webdriver_js_execute_code)

                    extra_wait = int(os.getenv("WEBDRIVER_DELAY_BEFORE_CONTENT_READY", 5)) + self.render_extract_delay
                    await page.wait_for_timeout(extra_wait * 1000)

                    # Auth / challenge detection (spec 10) - BEFORE extracting content.
                    title = await page.title()
                    text = await page.evaluate("document.body ? document.body.innerText : ''")
                    html = await page.content()
                    challenge = detect_auth_challenge(
                        url=url, status_code=self.status_code,
                        page_title=title, page_text=text, page_html=html,
                    )
                    if challenge['required']:
                        # Keep the page; block the gate; let the worker exit cleanly.
                        gate.enter_attention(watch_uuid)
                        attention = True
                        raise LocalChromeAttentionRequired(challenge['reasons'], watch_uuid=watch_uuid)

                    if self.status_code != 200 and not ignore_status_codes:
                        screenshot = await capture_full_page_async(page, screenshot_format=self.screenshot_format, watch_uuid=watch_uuid)
                        raise Non200ErrorCodeReceived(url=url, status_code=self.status_code, screenshot=screenshot)

                    if fetch_favicon:
                        try:
                            self.favicon_blob = await page.evaluate(FAVICON_FETCHER_JS)
                        except Exception as e:
                            logger.error(f"Error fetching favicon: {e}")

                    if not empty_pages_are_a_change and len(text.strip()) == 0:
                        raise EmptyReply(url=url, status_code=self.status_code)

                    if self.browser_steps:
                        await self.iterate_browser_steps(start_url=url)
                        await page.wait_for_timeout(extra_wait * 1000)

                    await page.evaluate("var include_filters={}".format(
                        json.dumps(current_include_filters) if current_include_filters else "''"))
                    self.xpath_data = await page.evaluate(XPATH_ELEMENT_JS, {
                        "visualselector_xpath_selectors": visualselector_xpath_selectors,
                        "max_height": int(os.getenv("SCREENSHOT_MAX_HEIGHT", SCREENSHOT_MAX_HEIGHT_DEFAULT)),
                    })
                    self.instock_data = await page.evaluate(INSTOCK_DATA_JS)
                    self.content = await page.content()
                    self.screenshot = await capture_full_page_async(page, screenshot_format=self.screenshot_format, watch_uuid=watch_uuid)

                except LocalChromeAttentionRequired:
                    # Keep the page; only the gate is blocked.
                    raise
                finally:
                    # Spec 9.2: keep the page when attention is required;
                    # close the page on normal completion/error.
                    if not attention:
                        try:
                            await page.close()
                        except Exception as e:
                            logger.warning(f"Error closing task page: {e}")
                    # Always disconnect the CDP client only; never close the
                    # persistent context or Chrome itself.
                    try:
                        await browser.close()
                    except Exception:
                        pass

    async def quit(self, watch=None):
        # Nothing to quit per-task: we only disconnected the CDP client in run().
        # The persistent context and Chrome are owned by LocalChromeManager.
        return

