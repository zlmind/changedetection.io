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
    EmptyReply, Non200ErrorCodeReceived, LocalChromeAttentionRequired,
)
from changedetectionio.content_fetchers.playwright import capture_full_page_async
from changedetectionio.local_browser import is_local_chrome_supported, is_local_chrome_enabled
from changedetectionio.local_browser.manager import get_manager, LocalChromeUnavailable
from changedetectionio.local_browser.task_gate import get_gate
from changedetectionio.local_browser.auth_detector import detect_auth_challenge

# Runtime map: watch_uuid -> CDP target_id, for paused (attention) tabs.
# In-memory only; changedetection.io owns the Chrome process and closes it on
# exit, so no cross-process recovery is needed (spec 9.2).
_target_ids: dict = {}


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

        async with gate.acquire(watch_uuid):
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(manager.cdp_endpoint(), timeout=60000)
                # Reuse the persistent default context; never new_context() (spec 9.1).
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                try:
                    target_id = await self._read_target_id(page)
                    if target_id:
                        _target_ids[watch_uuid] = target_id
                except Exception as e:
                    logger.debug(f"Could not read CDP target id: {e}")

                attention = False
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=(timeout or 45) * 1000)
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
                    # Keep the page + target-id mapping; only the gate is blocked.
                    raise
                finally:
                    # Spec 9.2: keep the page + mapping when attention is required;
                    # close the page + drop the mapping on normal completion/error.
                    if not attention:
                        try:
                            await page.close()
                        except Exception as e:
                            logger.warning(f"Error closing task page: {e}")
                        finally:
                            _target_ids.pop(watch_uuid, None)
                    # Always disconnect the CDP client only; never close the
                    # persistent context or Chrome itself.
                    try:
                        await browser.close()
                    except Exception:
                        pass

    async def _read_target_id(self, page):
        # Playwright exposes a CDP session per page; target id is on the session.
        try:
            client = await page.context.new_cdp_session(page)
            return getattr(client, '_target_id', None)
        except Exception:
            return None

    async def quit(self, watch=None):
        # Nothing to quit per-task: we only disconnected the CDP client in run().
        # The persistent context and Chrome are owned by LocalChromeManager.
        return


# Plugin registration mirrors the other built-in fetchers.
class LocalChromeFetcherPlugin:
    def register_content_fetcher(self):
        return ('html_local_chrome', fetcher)


local_chrome_plugin = LocalChromeFetcherPlugin()
