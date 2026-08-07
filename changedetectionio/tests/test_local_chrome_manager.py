import os
import pytest

from changedetectionio.local_browser import manager as manager_module
from changedetectionio.local_browser.manager import LocalChromeManager, LocalChromeUnavailable


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


def test_owns_process_requires_pid_exe_and_user_data_dir(manager, monkeypatch):
    # All three checks pass -> True
    monkeypatch.setattr(manager_module, "_process_exists", lambda pid: True)
    monkeypatch.setattr(manager_module, "_process_exe", lambda pid: r"C:\chrome.exe")
    monkeypatch.setattr(manager_module, "_process_cmdline", lambda pid: [r"C:\chrome.exe", f"--user-data-dir={manager.profile_dir}"])
    assert manager.owns_process(pid=1234, expected_exe=r"C:\chrome.exe") is True


def test_owns_process_false_when_pid_missing(manager, monkeypatch):
    monkeypatch.setattr(manager_module, "_process_exists", lambda pid: False)
    assert manager.owns_process(pid=1234, expected_exe=r"C:\chrome.exe") is False


def test_owns_process_false_when_exe_mismatch(manager, monkeypatch):
    monkeypatch.setattr(manager_module, "_process_exists", lambda pid: True)
    monkeypatch.setattr(manager_module, "_process_exe", lambda pid: r"C:\something-else.exe")
    assert manager.owns_process(pid=1234, expected_exe=r"C:\chrome.exe") is False


def test_owns_process_false_when_user_data_dir_not_in_cmdline(manager, monkeypatch):
    monkeypatch.setattr(manager_module, "_process_exists", lambda pid: True)
    monkeypatch.setattr(manager_module, "_process_exe", lambda pid: r"C:\chrome.exe")
    monkeypatch.setattr(manager_module, "_process_cmdline", lambda pid: [r"C:\chrome.exe", "--user-data-dir=C:\\other"])
    assert manager.owns_process(pid=1234, expected_exe=r"C:\chrome.exe") is False


def test_ensure_running_starts_chrome_and_reads_port(manager, monkeypatch):
    started = {}
    def fake_popen(args, **kwargs):
        started["args"] = args
        class _P:
            pid = 4242
        return _P()
    monkeypatch.setattr(manager_module.subprocess, "Popen", fake_popen)
    # Simulate Chrome writing the port file after launch.
    def fake_ensure_port_file(self):
        os.makedirs(self.profile_dir, exist_ok=True)
        with open(os.path.join(self.profile_dir, "DevToolsActivePort"), "w") as f:
            f.write("5050\n/devtools/browser/x\n")
        return 5050
    monkeypatch.setattr(LocalChromeManager, "_wait_for_devtools_port", fake_ensure_port_file, raising=True)
    monkeypatch.setattr(manager_module, "_process_exists", lambda pid: True)
    monkeypatch.setattr(manager_module, "_process_exe", lambda pid: r"C:\chrome.exe")
    monkeypatch.setattr(manager_module, "_process_cmdline", lambda pid: [r"C:\chrome.exe", f"--user-data-dir={manager.profile_dir}"])

    manager.ensure_running(chrome_path=r"C:\chrome.exe")
    assert manager._running is True
    assert manager._pid == 4242
    assert manager._cdp_port == 5050
    assert "--remote-debugging-port=0" in started["args"]


def test_ensure_running_is_idempotent_when_already_running(manager, monkeypatch):
    calls = {"n": 0}
    def fake_popen(args, **kwargs):
        calls["n"] += 1
        class _P:
            pid = 4242
        return _P()
    monkeypatch.setattr(manager_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(LocalChromeManager, "_wait_for_devtools_port", lambda self: 5050)
    monkeypatch.setattr(manager_module, "_process_exists", lambda pid: True)
    monkeypatch.setattr(manager_module, "_process_exe", lambda pid: r"C:\chrome.exe")
    monkeypatch.setattr(manager_module, "_process_cmdline", lambda pid: [r"C:\chrome.exe", f"--user-data-dir={manager.profile_dir}"])

    manager.ensure_running(chrome_path=r"C:\chrome.exe")
    manager.ensure_running(chrome_path=r"C:\chrome.exe")
    assert calls["n"] == 1  # second call did not relaunch


def test_ensure_running_restarts_once_when_chrome_was_closed(manager, monkeypatch):
    launches = {"n": 0}
    def fake_popen(args, **kwargs):
        launches["n"] += 1
        class _P:
            pid = 7000 + launches["n"]
        return _P()
    monkeypatch.setattr(manager_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(LocalChromeManager, "_wait_for_devtools_port", lambda self: 5050)
    # First state: manager thinks it ran (pid 7000) but the process is gone.
    manager._pid = 7000
    manager._running = True
    # owns_process returns False because process is gone.
    monkeypatch.setattr(manager_module, "_process_exists", lambda pid: False)
    manager.ensure_running(chrome_path=r"C:\chrome.exe")
    assert launches["n"] == 1
    assert manager._pid == 7001


def test_stop_only_kills_owned_process(manager, monkeypatch):
    killed = []
    def fake_terminate(pid):
        killed.append(pid)
    monkeypatch.setattr(manager_module, "_terminate_pid", fake_terminate)
    monkeypatch.setattr(manager_module, "_process_exists", lambda pid: True)
    monkeypatch.setattr(manager_module, "_process_exe", lambda pid: r"C:\chrome.exe")
    monkeypatch.setattr(manager_module, "_process_cmdline", lambda pid: [r"C:\chrome.exe", f"--user-data-dir={manager.profile_dir}"])
    manager._pid = 9000
    manager._running = True
    manager._chrome_path = r"C:\chrome.exe"
    manager.stop()
    assert killed == [9000]
    assert manager._running is False


def test_stop_does_not_kill_unowned_process(manager, monkeypatch):
    killed = []
    monkeypatch.setattr(manager_module, "_terminate_pid", lambda pid: killed.append(pid))
    # owns_process False -> must not terminate.
    monkeypatch.setattr(manager_module, "_process_exists", lambda pid: False)
    manager._pid = 9000
    manager._running = True
    manager._chrome_path = r"C:\chrome.exe"
    manager.stop()
    assert killed == []


def test_cdp_endpoint_after_running(manager, monkeypatch):
    monkeypatch.setattr(manager_module.subprocess, "Popen", lambda args, **kw: type("P", (), {"pid": 7777})())
    monkeypatch.setattr(LocalChromeManager, "_wait_for_devtools_port", lambda self: 5050)
    monkeypatch.setattr(manager_module, "_process_exists", lambda pid: True)
    monkeypatch.setattr(manager_module, "_process_exe", lambda pid: r"C:\chrome.exe")
    monkeypatch.setattr(manager_module, "_process_cmdline", lambda pid: [r"C:\chrome.exe", f"--user-data-dir={manager.profile_dir}"])
    manager.ensure_running(chrome_path=r"C:\chrome.exe")
    assert manager.cdp_endpoint() == "http://127.0.0.1:5050"


def test_wait_for_devtools_port_returns_none_on_timeout(manager, monkeypatch):
    # parse_devtools_active_port always returns None -> the poll loop times out.
    monkeypatch.setattr(LocalChromeManager, "parse_devtools_active_port", lambda self: None)
    # No-op sleep so the test is fast.
    monkeypatch.setattr(manager_module.time, "sleep", lambda s: None)
    result = manager._wait_for_devtools_port(timeout=0.01, interval=0.001)
    assert result is None


def test_ensure_running_raises_when_devtools_port_not_ready(manager, monkeypatch):
    monkeypatch.setattr(manager_module.subprocess, "Popen", lambda args, **kw: type("P", (), {"pid": 4242})())
    monkeypatch.setattr(LocalChromeManager, "_wait_for_devtools_port", lambda self: None)
    with pytest.raises(LocalChromeUnavailable):
        manager.ensure_running(chrome_path=r"C:\chrome.exe")
    assert manager._running is False
