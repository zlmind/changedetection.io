import sys
import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == 'win32',
    reason="live_server multiprocessing pickling on Windows; runs on Linux CI",
)


def test_settings_page_lists_local_chrome_choice_when_enabled(client, live_server, monkeypatch):
    # Feature supported+enabled -> html_local_chrome appears in the fetch choices.
    from changedetectionio import local_browser
    monkeypatch.setattr(local_browser, '_PLATFORM', 'win32')
    from changedetectionio.flask_app import datastore as ds
    ds.data['settings']['requests']['local_chrome']['enabled'] = True
    res = client.get('/settings')
    assert b'html_local_chrome' in res.data


def test_settings_post_persists_local_chrome_enabled(client, live_server, monkeypatch):
    from changedetectionio import local_browser
    from changedetectionio.flask_app import datastore as ds
    monkeypatch.setattr(local_browser, '_PLATFORM', 'win32')
    res = client.post('/settings', data={
        'requests-local_chrome-enabled': 'y',
        'requests-local_chrome-chrome_executable': '',
        'save_button': 'Save',
    }, follow_redirects=True)
    assert res.status_code == 200
    assert ds.data['settings']['requests']['local_chrome']['enabled'] is True


def test_watch_edit_shows_local_chrome_choice_when_enabled(client, live_server, monkeypatch):
    from changedetectionio import local_browser
    monkeypatch.setattr(local_browser, '_PLATFORM', 'win32')
    from changedetectionio.flask_app import datastore as ds
    ds.data['settings']['requests']['local_chrome']['enabled'] = True
    uuid = ds.add_watch(url='https://example.com')
    res = client.get(f'/edit/{uuid}')
    assert res.status_code == 200
    assert b'html_local_chrome' in res.data


def test_watch_edit_shows_error_when_watch_uses_unavailable_local_chrome(client, live_server, monkeypatch):
    from changedetectionio import local_browser
    monkeypatch.setattr(local_browser, '_PLATFORM', 'linux')  # unsupported
    from changedetectionio.flask_app import datastore as ds
    uuid = ds.add_watch(url='https://example.com', extras={'fetch_backend': 'html_local_chrome'})
    res = client.get(f'/edit/{uuid}')
    assert res.status_code == 200
    assert b'Local Chrome' in res.data
    assert b'not available' in res.data.lower()

