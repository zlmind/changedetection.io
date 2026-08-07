import asyncio
import pytest

from changedetectionio.local_browser.manager import LocalChromeUnavailable
from changedetectionio.content_fetchers.exceptions import LocalChromeAttentionRequired


@pytest.fixture(autouse=True)
def prepare_test_function():
    """Override conftest's live_server-dependent autouse fixture for pure unit tests."""
    yield


def test_fetcher_description_and_flags():
    from changedetectionio.content_fetchers.local_chrome import fetcher
    assert fetcher.fetcher_description == "Local Chrome - Persistent Profile"
    assert fetcher.supports_browser_steps is True
    assert fetcher.supports_screenshots is True
    assert fetcher.supports_xpath_element_data is True
    assert fetcher.selectable_in_ui is False


def test_run_raises_when_unsupported(monkeypatch):
    from changedetectionio.content_fetchers import local_chrome as mod
    monkeypatch.setattr(mod, "is_local_chrome_supported", lambda: False)
    f = mod.fetcher()
    with pytest.raises(LocalChromeUnavailable):
        asyncio.run(f.run(url="https://example.com", watch_uuid="w1"))


def test_run_raises_when_disabled(monkeypatch):
    from changedetectionio.content_fetchers import local_chrome as mod
    monkeypatch.setattr(mod, "is_local_chrome_supported", lambda: True)
    monkeypatch.setattr(mod, "is_local_chrome_enabled", lambda ds: False)
    f = mod.fetcher()
    f._datastore = type("_DS", (), {"data": {'settings': {'requests': {'local_chrome': {'enabled': False}}}}})()
    with pytest.raises(LocalChromeUnavailable):
        asyncio.run(f.run(url="https://example.com", watch_uuid="w1"))


def _install_fakes(monkeypatch, closed):
    """Wire fake manager + fake Playwright so no real Chrome is touched."""
    from changedetectionio.content_fetchers import local_chrome as mod
    monkeypatch.setattr(mod, "is_local_chrome_supported", lambda: True)
    monkeypatch.setattr(mod, "is_local_chrome_enabled", lambda ds: True)

    class _FakeMgr:
        def ensure_running(self, chrome_path):
            return 54321
        def cdp_endpoint(self):
            return "http://127.0.0.1:54321"
        def find_chrome_executable(self, custom_path=None):
            return r"C:\chrome.exe"
    monkeypatch.setattr(mod, "get_manager", lambda datastore_path=None: _FakeMgr())

    class _FakeResponse:
        status = 200
        async def all_headers(self):
            return {"content-type": "text/html"}

    class _FakePage:
        def __init__(self):
            self.viewport_size = {"width": 1280, "height": 720}
        async def evaluate(self, script, *args, **kw):
            if "scrollHeight" in script: return 100
            if "scrollWidth" in script: return 100
            if "innerText" in script: return "ok content"
            return ""
        async def content(self):
            return "<html><body>ok</body></html>"
        async def screenshot(self, **kw):
            return b""
        async def request_gc(self):
            pass
        async def close(self):
            closed["page"] += 1
        async def goto(self, url, **kw):
            return _FakeResponse()
        async def title(self):
            return "Page Title"
        async def set_viewport_size(self, size):
            self.viewport_size = dict(size)
        async def wait_for_timeout(self, ms):
            pass
        def on(self, *a, **kw):
            pass

    class _FakeContext:
        async def new_page(self):
            return _FakePage()
        async def close(self):
            closed["context"] += 1

    class _FakeBrowser:
        contexts = [_FakeContext()]
        async def close(self):
            closed["browser"] += 1

    class _FakeCDP:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        @property
        def chromium(self):
            return self
        async def connect_over_cdp(self, url, **kw):
            return _FakeBrowser()

    import playwright.async_api as pwapi
    monkeypatch.setattr(pwapi, "async_playwright", lambda: _FakeCDP())

    from changedetectionio.local_browser.task_gate import reset_gate_for_tests
    reset_gate_for_tests()


def test_run_uses_persistent_context_and_closes_task_page(monkeypatch):
    """On success the task page is closed; the persistent context and Chrome are NOT closed."""
    from changedetectionio.content_fetchers import local_chrome as mod
    closed = {"page": 0, "context": 0, "browser": 0}
    _install_fakes(monkeypatch, closed)

    f = mod.fetcher()
    f.webdriver_js_execute_code = None
    f.screenshot_format = "JPEG"
    asyncio.run(f.run(url="https://example.com/page", watch_uuid="w1"))

    assert closed["page"] == 1      # task page closed
    assert closed["context"] == 0   # persistent context NOT closed
    assert closed["browser"] == 1   # CDP client disconnected (browser.close on the connection only)


def test_run_keeps_page_on_attention(monkeypatch):
    """When an auth challenge is detected, the page is KEPT, the gate enters attention,
    and LocalChromeAttentionRequired is raised (spec 9.2)."""
    from changedetectionio.content_fetchers import local_chrome as mod
    closed = {"page": 0, "context": 0, "browser": 0}
    _install_fakes(monkeypatch, closed)

    # Override the fake page's title/text to look like a login page (2 weak signals
    # -> auth_detector returns required=True). We do this by patching detect_auth_challenge
    # directly to force an attention result, isolating this test from detector details.
    reasons = ["URL entered an authentication path (login/signin/auth)"]
    def _fake_detect(**kwargs):
        return {"required": True, "reasons": reasons}
    monkeypatch.setattr(mod, "detect_auth_challenge", _fake_detect)

    f = mod.fetcher()
    f.webdriver_js_execute_code = None
    f.screenshot_format = "JPEG"
    with pytest.raises(LocalChromeAttentionRequired) as exc_info:
        asyncio.run(f.run(url="https://example.com/login", watch_uuid="w-attn"))

    assert exc_info.value.watch_uuid == "w-attn"
    assert closed["page"] == 0       # page KEPT (not closed) on attention
    assert closed["context"] == 0    # context never closed
    assert closed["browser"] == 1    # CDP client still disconnected
    from changedetectionio.local_browser.task_gate import get_gate
    assert get_gate().attention_watch_uuid() == "w-attn"  # gate in attention state
    get_gate().resolve_attention()  # cleanup


def test_run_sets_self_page_for_browser_steps(monkeypatch):
    """self.page must be set to the task page so iterate_browser_steps() drives it
    (the base Fetcher reads self.page; without it steps silently no-op)."""
    from changedetectionio.content_fetchers import local_chrome as mod
    closed = {"page": 0, "context": 0, "browser": 0}
    _install_fakes(monkeypatch, closed)

    f = mod.fetcher()
    f.webdriver_js_execute_code = None
    f.screenshot_format = "JPEG"
    f.browser_steps = []  # avoid actually iterating; we only assert self.page
    asyncio.run(f.run(url="https://example.com/page", watch_uuid="w-steps"))

    assert f.page is not None


def test_run_forwards_datastore_path_to_manager(monkeypatch):
    """The manager singleton must be created with the datastore path so profile_dir
    derives from the datastore (spec 7.1), not the process CWD."""
    from changedetectionio.content_fetchers import local_chrome as mod
    seen = {"path": None}

    class _FakeMgr:
        def ensure_running(self, chrome_path):
            return 54321
        def cdp_endpoint(self):
            return "http://127.0.0.1:54321"
        def find_chrome_executable(self, custom_path=None):
            return r"C:\chrome.exe"

    # Install hermetic gate/pwapi fakes first, then capture get_manager's arg.
    _install_fakes(monkeypatch, {"page": 0, "context": 0, "browser": 0})
    monkeypatch.setattr(
        mod, "get_manager",
        lambda datastore_path=None: seen.__setitem__("path", datastore_path) or _FakeMgr(),
    )

    class _DS:
        def __init__(self, path):
            self.datastore_path = path
            self.data = {'settings': {'requests': {'local_chrome': {'enabled': True, 'chrome_executable': None}}}}

    f = mod.fetcher()
    f._datastore = _DS(r"D:\datastore")
    f.webdriver_js_execute_code = None
    f.screenshot_format = "JPEG"
    asyncio.run(f.run(url="https://example.com", watch_uuid="w-path"))

    assert seen["path"] == r"D:\datastore"
