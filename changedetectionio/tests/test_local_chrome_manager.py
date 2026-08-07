import os
import pytest

from changedetectionio.local_browser.manager import LocalChromeManager


@pytest.fixture(autouse=True)
def prepare_test_function():
    """Override conftest's live_server-dependent autouse fixture for pure unit tests."""
    yield


@pytest.fixture
def manager(tmp_path, monkeypatch):
    # Patch the _PLATFORM that is_local_chrome_supported() actually reads, so
    # these tests pass on Linux CI too - not just on a Windows dev machine.
    monkeypatch.setattr('changedetectionio.local_browser._PLATFORM', 'win32')
    return LocalChromeManager(datastore_path=str(tmp_path))


def test_find_chrome_uses_custom_path_when_file_exists(manager, tmp_path):
    fake = tmp_path / "chrome.exe"
    fake.write_text("x")
    assert manager.find_chrome_executable(custom_path=str(fake)) == str(fake)


def test_find_chrome_rejects_missing_custom_path(manager):
    with pytest.raises(FileNotFoundError):
        manager.find_chrome_executable(custom_path="C:\\does-not-exist\\chrome.exe")


def test_find_chrome_searches_default_windows_locations(manager, monkeypatch):
    from changedetectionio.local_browser.manager import _DEFAULT_CHROME_PATHS_WIN
    seen = []

    def fake_isfile(path):
        seen.append(path)
        # Pretend the 2nd default location (Program Files) exists.
        return path == _DEFAULT_CHROME_PATHS_WIN[1]

    monkeypatch.setattr(os.path, "isfile", fake_isfile)
    found = manager.find_chrome_executable(custom_path=None)
    assert found == _DEFAULT_CHROME_PATHS_WIN[1]
    # Checked locations in order and stopped at the first hit (candidate 0 miss, candidate 1 hit).
    assert seen == _DEFAULT_CHROME_PATHS_WIN[:2]


def test_find_chrome_raises_when_not_found_anywhere(manager, monkeypatch):
    monkeypatch.setattr(os.path, "isfile", lambda p: False)
    with pytest.raises(FileNotFoundError):
        manager.find_chrome_executable(custom_path=None)
