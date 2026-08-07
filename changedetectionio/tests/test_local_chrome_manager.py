import os
import pytest

from changedetectionio.local_browser import manager as manager_module
from changedetectionio.local_browser.manager import LocalChromeManager


@pytest.fixture(autouse=True)
def prepare_test_function():
    """Override conftest's live_server-dependent autouse fixture for pure unit tests."""
    yield


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_module, '_PLATFORM', 'win32')
    return LocalChromeManager(datastore_path=str(tmp_path))


def test_find_chrome_uses_custom_path_when_file_exists(manager, tmp_path):
    fake = tmp_path / "chrome.exe"
    fake.write_text("x")
    assert manager.find_chrome_executable(custom_path=str(fake)) == str(fake)


def test_find_chrome_rejects_missing_custom_path(manager):
    with pytest.raises(FileNotFoundError):
        manager.find_chrome_executable(custom_path="C:\\does-not-exist\\chrome.exe")


def test_find_chrome_searches_default_windows_locations(manager, monkeypatch):
    seen = []

    def fake_exists(path):
        seen.append(path)
        return path.endswith("Program Files\\Google\\Chrome\\Application\\chrome.exe")

    monkeypatch.setattr(os.path, "exists", fake_exists)
    found = manager.find_chrome_executable(custom_path=None)
    assert found.endswith("Program Files\\Google\\Chrome\\Application\\chrome.exe")
    # Must have checked the common locations in order.
    assert any("AppData" in p for p in seen)
    assert any("Program Files (x86)" in p for p in seen) or any("Program Files\\Google" in p for p in seen)


def test_find_chrome_raises_when_not_found_anywhere(manager, monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    with pytest.raises(FileNotFoundError):
        manager.find_chrome_executable(custom_path=None)
