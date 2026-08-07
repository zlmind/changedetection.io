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


def test_profile_dir_is_under_datastore(manager):
    assert manager.profile_dir.endswith("browser-profile")
    assert os.path.dirname(manager.profile_dir) == manager.datastore_path


def test_build_startup_args_uses_dedicated_profile_and_loopback(manager, tmp_path, monkeypatch):
    args = manager.build_startup_args(chrome_path="C:\\chrome.exe")
    assert args[0] == "C:\\chrome.exe"
    joined = " ".join(args)
    assert f"--user-data-dir={manager.profile_dir}" in joined
    assert "--remote-debugging-address=127.0.0.1" in joined
    assert "--remote-debugging-port=0" in joined
    # Must NOT inherit CHROME_OPTIONS env-based args.
    monkeypatch.setenv("CHROME_OPTIONS", "--no-sandbox\n--headless")
    args2 = manager.build_startup_args(chrome_path="C:\\chrome.exe")
    assert "--no-sandbox" not in " ".join(args2)
    assert "--headless" not in " ".join(args2)
    # Visible window: no --headless flag added by us.
    assert "--headless" not in " ".join(args)


def test_parse_devtools_active_port_returns_port(manager, tmp_path):
    port_file = manager.profile_dir
    os.makedirs(port_file, exist_ok=True)
    path = os.path.join(port_file, "DevToolsActivePort")
    with open(path, "w", encoding="utf-8") as f:
        f.write("52341\n/devtools/browser/abc-123\n")
    assert manager.parse_devtools_active_port() == 52341


def test_parse_devtools_active_port_missing_returns_none(manager):
    assert manager.parse_devtools_active_port() is None


def test_parse_devtools_active_port_garbage_returns_none(manager, tmp_path):
    os.makedirs(manager.profile_dir, exist_ok=True)
    path = os.path.join(manager.profile_dir, "DevToolsActivePort")
    with open(path, "w", encoding="utf-8") as f:
        f.write("not-a-number\n")
    assert manager.parse_devtools_active_port() is None
