"""Regression guards for the Local Chrome backend.

Pure unit tests (no live_server) - they shadow conftest's live_server-dependent
autouse fixture so they run on any platform. The single live_server UI check is
skipped on Windows (pytest-flask pickling).
"""
import sys

import pytest


@pytest.fixture(autouse=True)
def prepare_test_function():
    """Override conftest's live_server-dependent autouse fixture."""
    yield


def test_stop_is_noop_without_owned_process():
    """stop() must not raise when no Chrome was ever started."""
    from changedetectionio.local_browser.manager import LocalChromeManager
    mgr = LocalChromeManager(datastore_path=".")
    # No PID set -> stop() is a no-op and must not raise.
    mgr.stop()
    assert mgr.status()['running'] is False


def test_non_windows_does_not_launch_chrome(monkeypatch):
    """On non-Windows, discovery and launch must raise, never start Chrome."""
    from changedetectionio import local_browser
    monkeypatch.setattr(local_browser, '_PLATFORM', 'linux')
    assert local_browser.is_local_chrome_supported() is False

    from changedetectionio.local_browser.manager import LocalChromeManager, LocalChromeUnavailable
    mgr = LocalChromeManager(datastore_path=".")
    with pytest.raises(LocalChromeUnavailable):
        mgr.find_chrome_executable()
    with pytest.raises(LocalChromeUnavailable):
        mgr.ensure_running(chrome_path=r"C:\chrome.exe")


def test_sigshutdown_handler_stops_owned_chrome(monkeypatch):
    """sigshutdown_handler must call get_manager().stop() and swallow errors."""
    from changedetectionio import local_browser
    import changedetectionio
    from changedetectionio.local_browser import manager as manager_module

    stopped = {"called": False}

    class _FakeMgr:
        def stop(self):
            stopped["called"] = True

    # Reset the singleton then prime it with the fake.
    manager_module.reset_manager_for_tests()
    monkeypatch.setattr(manager_module, "get_manager", lambda datastore_path=None: _FakeMgr())

    # The handler touches module-level app/datastore/queues/socketio; stub the
    # heavy pieces so we only exercise the Local Chrome stop block.
    import types

    class _Ev:
        def set(self):
            pass

    fake_app = types.SimpleNamespace(config=types.SimpleNamespace(exit=_Ev()))
    monkeypatch.setattr(changedetectionio, "app", fake_app, raising=False)

    fake_datastore = types.SimpleNamespace(stop_thread=True)
    monkeypatch.setattr(changedetectionio, "datastore", fake_datastore, raising=False)

    # Stub the worker_pool / flask_app imports the handler performs.
    import sys
    fake_worker_pool = types.ModuleType("changedetectionio.worker_pool")
    fake_worker_pool.shutdown_workers = lambda: None
    monkeypatch.setitem(sys.modules, "changedetectionio.worker_pool", fake_worker_pool)

    fake_flask_app = types.ModuleType("changedetectionio.flask_app")
    fake_flask_app.update_q = types.SimpleNamespace(close=lambda: None)
    fake_flask_app.notification_q = types.SimpleNamespace(close=lambda: None)
    fake_flask_app.socketio_server = None
    monkeypatch.setitem(sys.modules, "changedetectionio.flask_app", fake_flask_app)

    with pytest.raises(SystemExit):
        changedetectionio.sigshutdown_handler(15, None)

    assert stopped["called"] is True


@pytest.mark.skipif(
    sys.platform == 'win32',
    reason="live_server multiprocessing pickling on Windows; runs on Linux CI",
)
def test_html_requests_still_resolves_and_runs(client, live_server):
    """html_requests behavior is unchanged (spec 17)."""
    from flask import url_for
    res = client.get(url_for("watchlist.index"))
    assert res.status_code == 200


def test_available_fetchers_still_lists_requests_and_webdriver():
    from changedetectionio import content_fetchers
    names = [n for n, _ in content_fetchers.available_fetchers()]
    assert 'html_requests' in names
    assert 'html_webdriver' in names


def test_disabled_local_chrome_never_calls_ensure_running(monkeypatch):
    """When disabled, resolve+run must raise LocalChromeUnavailable, not launch Chrome."""
    from changedetectionio.content_fetchers import local_chrome as mod

    monkeypatch.setattr(mod, "is_local_chrome_supported", lambda: True)
    monkeypatch.setattr(mod, "is_local_chrome_enabled", lambda ds: False)

    called = {"ensure": False}

    class _BadMgr:
        def ensure_running(self, chrome_path):
            called["ensure"] = True

        def find_chrome_executable(self, custom_path=None):
            return r"C:\chrome.exe"

    monkeypatch.setattr(mod, "get_manager", lambda datastore_path=None: _BadMgr())

    from changedetectionio.local_browser.manager import LocalChromeUnavailable
    f = mod.fetcher()
    # _check_available only consults the enabled flag when a datastore is set;
    # is_local_chrome_enabled is monkeypatched to False, so this must raise
    # before any manager launch call.
    f._datastore = object()
    import asyncio
    with pytest.raises(LocalChromeUnavailable):
        asyncio.run(f.run(url="https://example.com", watch_uuid="w"))
    assert called["ensure"] is False  # never launched
