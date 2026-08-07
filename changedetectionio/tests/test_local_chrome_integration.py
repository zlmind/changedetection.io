"""Real-Windows-Chrome integration tests for the Local Chrome backend.

Skipped unless RUN_LOCAL_CHROME_INTEGRATION=1 is set (and on Windows). These do
NOT run in normal Linux CI (spec 16.2).
"""
import os
import sys
import asyncio

import pytest

pytestmark = pytest.mark.skipif(
    not (sys.platform == 'win32' and os.getenv('RUN_LOCAL_CHROME_INTEGRATION') == '1'),
    reason="Real Chrome integration test; set RUN_LOCAL_CHROME_INTEGRATION=1 on Windows to run.",
)


def _has_chrome():
    from changedetectionio.local_browser.manager import LocalChromeManager
    try:
        LocalChromeManager(datastore_path=os.path.expandvars(r'%APPDATA%\changedetection.io')).find_chrome_executable()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def manager(tmp_path_factory):
    from changedetectionio.local_browser.manager import LocalChromeManager
    m = LocalChromeManager(datastore_path=str(tmp_path_factory.mktemp("lc-profile")))
    yield m
    m.stop()


def test_chrome_starts_and_persistent_context_reused(manager):
    if not _has_chrome():
        pytest.skip("Google Chrome not installed")
    port = manager.ensure_running(chrome_path=manager.find_chrome_executable())
    assert isinstance(port, int) and port > 0

    async def _go():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.connect_over_cdp(manager.cdp_endpoint(), timeout=30000)
            ctx = b.contexts[0]
            page = await ctx.new_page()
            await page.goto("about:blank")
            # Write a localStorage value to prove persistence.
            await page.evaluate("localStorage.setItem('cd_test','persist-me')")
            await page.close()
            await b.close()
    asyncio.run(_go())

    # Second connection: read back the value from the same persistent context.
    async def _read():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.connect_over_cdp(manager.cdp_endpoint(), timeout=30000)
            ctx = b.contexts[0]
            page = await ctx.new_page()
            await page.goto("about:blank")
            val = await page.evaluate("localStorage.getItem('cd_test')")
            await page.close()
            await b.close()
            return val
    assert asyncio.run(_read()) == "persist-me"


def test_login_page_triggers_attention(manager):
    if not _has_chrome():
        pytest.skip("Google Chrome not installed")
    from changedetectionio.local_browser.auth_detector import detect_auth_challenge
    r = detect_auth_challenge(url="https://example.com/login", status_code=200,
                              page_title="Login", page_text="please log in",
                              page_html="<input type='password'/>")
    assert r['required'] is True
