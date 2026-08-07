import tempfile

import pytest

from changedetectionio.model.App import model


@pytest.fixture(autouse=True)
def prepare_test_function():
    """Override the conftest's live_server-dependent autouse fixture.

    These are pure unit tests of the App.model config defaults and do not
    need the Flask live server (which cannot spawn on Windows due to a
    pytest_flask multiprocessing pickling bug).
    """
    yield


def test_local_chrome_defaults_present():
    with tempfile.TemporaryDirectory() as path:
        app = model(datastore_path=path)
        lc = app['settings']['requests']['local_chrome']
        assert lc['enabled'] is False
        assert lc['chrome_executable'] is None


def test_local_chrome_defaults_merged_from_disk():
    # Simulate an old config file that has no local_chrome key yet.
    with tempfile.TemporaryDirectory() as path:
        stored = {'settings': {'requests': {'timeout': 45}}}
        # Mirror how _apply_settings loads an old config: start from a fresh
        # model (with defaults) and .update() its requests with the partial
        # dict, confirming a pre-local_chrome config won't clobber the new defaults.
        app2 = model(datastore_path=path)
        app2['settings']['requests'].update(stored['settings']['requests'])
        assert app2['settings']['requests']['local_chrome']['enabled'] is False


def test_is_local_chrome_supported_reflects_platform(monkeypatch):
    from changedetectionio import local_browser
    monkeypatch.setattr(local_browser, '_PLATFORM', 'win32')
    assert local_browser.is_local_chrome_supported() is True
    monkeypatch.setattr(local_browser, '_PLATFORM', 'linux')
    assert local_browser.is_local_chrome_supported() is False


def test_is_local_chrome_enabled_reads_datastore():
    from changedetectionio.local_browser import is_local_chrome_enabled

    class _DS:
        data = {'settings': {'requests': {'local_chrome': {'enabled': True}}}}
    assert is_local_chrome_enabled(_DS()) is True

    class _DSOff:
        data = {'settings': {'requests': {'local_chrome': {'enabled': False}}}}
    assert is_local_chrome_enabled(_DSOff()) is False

    class _DSMissing:
        data = {'settings': {'requests': {}}}
    assert is_local_chrome_enabled(_DSMissing()) is False


def test_html_local_chrome_hidden_from_default_available_fetchers():
    from changedetectionio import content_fetchers
    names = [n for n, _ in content_fetchers.available_fetchers()]
    assert 'html_local_chrome' not in names  # gated; views add it when enabled


def test_resolve_content_fetcher_finds_html_local_chrome():
    """Already-configured watches still resolve to the class (so it can error cleanly)."""
    from changedetectionio import content_fetchers
    from changedetectionio.content_fetchers.local_chrome import fetcher as lc_fetcher

    class _Watch(dict):
        pass
    class _DS:
        data = {'settings': {'application': {'fetch_backend': 'html_requests'}}}
    w = _Watch(fetch_backend='html_local_chrome')
    obj, name, url = content_fetchers.resolve_content_fetcher(watch=w, datastore=_DS())
    assert obj is lc_fetcher
    assert name == 'html_local_chrome'


def test_local_chrome_attention_message_is_human_readable():
    from changedetectionio.content_fetchers.exceptions import LocalChromeAttentionRequired
    e = LocalChromeAttentionRequired(["URL entered an authentication path", "HTTP status 401"], watch_uuid="w")
    msg = str(e)
    assert "authentication path" in msg
    assert "401" in msg


def test_local_chrome_unavailable_message():
    from changedetectionio.local_browser.manager import LocalChromeUnavailable
    e = LocalChromeUnavailable("Local Chrome is disabled in Settings.")
    assert "disabled" in str(e)


def test_fetcher_datastore_injected_in_call_browser():
    """call_browser() must set fetcher._datastore so the fetcher can read the enabled flag."""
    import inspect
    from changedetectionio.processors.base import difference_detection_processor
    src = inspect.getsource(difference_detection_processor.call_browser)
    assert "_datastore" in src
    assert "self.datastore" in src


def test_global_settings_form_has_local_chrome_fields():
    from changedetectionio import forms
    import tempfile
    from changedetectionio.model.App import model
    with tempfile.TemporaryDirectory() as p:
        default = model(datastore_path=p)
        data = {
            'application': default['settings']['application'],
            'requests': default['settings']['requests'],
            'llm': {'api_key': ''},
        }
        form = forms.globalSettingsForm(data=data, extra_notification_tokens={})
        assert hasattr(form.requests.form, 'local_chrome')
        assert hasattr(form.requests.form.local_chrome.form, 'enabled')
        assert hasattr(form.requests.form.local_chrome.form, 'chrome_executable')
