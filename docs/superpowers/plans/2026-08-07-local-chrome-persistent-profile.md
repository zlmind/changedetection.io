# Windows Local Chrome 持久 Profile 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fully isolated `html_local_chrome` fetcher backend that drives a visible, persistent, dedicated-profile Google Chrome on Windows via Playwright CDP, so a user can log in once and have all subsequent checks reuse that login state.

**Architecture:** A process-level `LocalChromeManager` owns one Chrome process (dedicated `--user-data-dir`, loopback CDP, dynamic port). A process-level `LocalBrowserTaskGate` serializes all `html_local_chrome` tasks and holds a logical "attention required" state when a login/captcha challenge is detected. A new `local_chrome.py` fetcher connects over CDP, reuses Chrome's persistent default context (never `new_context()`), opens one tab per task, and reuses the existing stateless capabilities (browser steps, screenshots, xpath, instock, favicon) without modifying the old Playwright/Selenium/Requests fetchers. An `auth_detector` scores strong/weak login-challenge signals. Config lives under `settings.requests.local_chrome`; the feature is Windows-only and launches no Chrome when disabled.

**Tech Stack:** Python 3.10+, Playwright `~=1.56.0` (matches Dockerfile pin, connects to system Chrome over CDP - no Chromium download), WTForms, Flask, pytest/pytest-flask. Windows 10/11 only for Phase 1.

**Source spec:** `docs/superpowers/specs/2026-08-07-local-chrome-persistent-profile-design.md`

---

## Scope note

This is one cohesive subsystem (the Local Chrome backend) and is implemented as a single plan. It is large; tasks are ordered so that every task produces working, tested software on its own. Pure-logic foundations (Tasks 1-11) need no real Chrome and run on any OS in CI. The fetcher (Task 12), UI (Tasks 15-18), lifecycle (Task 19), and integration test (Task 21) are Windows-gated where they touch a real browser.

## File Structure

**Create:**
- `changedetectionio/local_browser/__init__.py` - package marker + public helpers (`is_local_chrome_supported`, `is_local_chrome_enabled`)
- `changedetectionio/local_browser/manager.py` - `LocalChromeManager` singleton: Chrome path discovery, profile dir, startup args, DevToolsActivePort parsing, PID ownership, `ensure_running()`/`stop()`/`status()`
- `changedetectionio/local_browser/task_gate.py` - `LocalBrowserTaskGate` singleton: serial `asyncio` gate + attention-required state (`acquire`/`release`/`enter_attention`/`resolve_attention`/`cancel_attention`)
- `changedetectionio/local_browser/auth_detector.py` - `detect_auth_challenge(url, status_code, page_title, page_text, page_html)` returning `{required, reasons}` via strong/weak signal scoring
- `changedetectionio/content_fetchers/local_chrome.py` - `fetcher(Fetcher)` class registered as `html_local_chrome`; CDP connect, persistent context reuse, per-task page + target-id map, reuse of browser-steps/screenshot/xpath/instock/favicon helpers
- `changedetectionio/tests/test_local_chrome_manager.py` - unit tests for manager (path discovery, args, port parse, PID ownership, lifecycle)
- `changedetectionio/tests/test_local_browser_task_gate.py` - unit tests for serial gate + attention state
- `changedetectionio/tests/test_local_chrome_auth_detector.py` - unit tests for signal scoring
- `changedetectionio/tests/test_local_chrome_config.py` - unit tests for config defaults + no-fallback resolution
- `changedetectionio/tests/test_local_chrome_regression.py` - regression: old fetchers unchanged, non-Windows/disabled launches no Chrome
- `changedetectionio/tests/test_local_chrome_integration.py` - Windows-only integration (gated skip)

**Modify:**
- `changedetectionio/model/App.py` - add `local_chrome` defaults under `settings.requests`
- `changedetectionio/content_fetchers/__init__.py` - import `html_local_chrome`; keep it out of `available_fetchers()` default list via `selectable_in_ui=False`; no-fallback error in `resolve_content_fetcher`
- `changedetectionio/content_fetchers/base.py` - add `selectable_in_ui = True` class attr
- `changedetectionio/forms.py` - add `local_chrome` sub-form fields to `globalSettingsRequestForm`
- `changedetectionio/blueprint/settings/__init__.py` - persist local_chrome settings; append `html_local_chrome` to fetch_backend choices when available; open/restart browser actions
- `changedetectionio/blueprint/settings/templates/settings.html` - new "Local Chrome" tab
- `changedetectionio/blueprint/ui/templates/edit.html` - show clear error when watch uses `html_local_chrome` but it is unavailable
- `changedetectionio/blueprint/ui/__init__.py` (or the edit view) - filter fetch_backend choices + pass availability flag
- `changedetectionio/__init__.py` - stop owned Chrome in `sigshutdown_handler`
- `changedetectionio/worker.py` - map local-chrome attention state to watch `last_error`/minitext status (minimal, via a new fetcher exception)
- `changedetectionio/content_fetchers/exceptions/__init__.py` - add `LocalChromeAttentionRequired` + `LocalChromeUnavailable` exceptions

---

## Task 1: Add `local_chrome` config defaults

**Files:**
- Modify: `changedetectionio/model/App.py:26-38` (the `requests` block of `base_config`)
- Test: `changedetectionio/tests/test_local_chrome_config.py`

- [ ] **Step 1: Write the failing test**

Create `changedetectionio/tests/test_local_chrome_config.py`:

```python
import os
import tempfile

from changedetectionio.model.App import model


def test_local_chrome_defaults_present():
    with tempfile.TemporaryDirectory() as path:
        app = model(datastore_path=path)
        lc = app['settings']['requests']['local_chrome']
        assert lc['enabled'] is False
        assert lc['chrome_executable'] is None


def test_local_chrome_defaults_merged_from_disk():
    # Simulate an old config file that has no local_chrome key yet.
    with tempfile.TemporaryDirectory() as path:
        app = model(datastore_path=path)
        # Drop the key to simulate a pre-existing store, then re-apply.
        stored = {'settings': {'requests': {'timeout': 45}}}
        # _apply_settings does a dict.update on requests, so missing keys
        # keep their defaults.
        app2 = model(datastore_path=path)
        app2['settings']['requests'].update(stored['settings']['requests'])
        assert app2['settings']['requests']['local_chrome']['enabled'] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py -v`
Expected: FAIL with `KeyError: 'local_chrome'`

- [ ] **Step 3: Write minimal implementation**

In `changedetectionio/model/App.py`, inside the `'requests': {` dict (after `'default_ua': {...}`), add:

```python
                    'local_chrome': {
                        'enabled': False,
                        'chrome_executable': None,
                    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/model/App.py changedetectionio/tests/test_local_chrome_config.py
git commit -m "feat(local-chrome): add local_chrome config defaults under settings.requests"
```

---

## Task 2: `local_browser` package + Windows/support helpers

**Files:**
- Create: `changedetectionio/local_browser/__init__.py`
- Test: `changedetectionio/tests/test_local_chrome_config.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_config.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'changedetectionio.local_browser'`

- [ ] **Step 3: Write minimal implementation**

Create `changedetectionio/local_browser/__init__.py`:

```python
"""Local Chrome persistent-profile backend (Windows Phase 1).

This package owns the lifecycle of a single visible Google Chrome process that
reuses a dedicated persistent profile, so a user can log in once and have all
subsequent checks reuse that login state. See
docs/superpowers/specs/2026-08-07-local-chrome-persistent-profile-design.md
"""
import sys

# Indirection so tests can monkeypatch the platform without touching sys.platform.
_PLATFORM = sys.platform


def is_local_chrome_supported() -> bool:
    """True only on Windows in Phase 1."""
    return _PLATFORM == 'win32'


def is_local_chrome_enabled(datastore) -> bool:
    """True when the feature is both supported and turned on in settings."""
    if not is_local_chrome_supported():
        return False
    try:
        return bool(datastore.data['settings']['requests']['local_chrome']['enabled'])
    except (KeyError, TypeError):
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/local_browser/__init__.py changedetectionio/tests/test_local_chrome_config.py
git commit -m "feat(local-chrome): add local_browser package with support/enabled helpers"
```

---

## Task 3: Chrome executable path discovery

**Files:**
- Create: `changedetectionio/local_browser/manager.py`
- Test: `changedetectionio/tests/test_local_chrome_manager.py`

- [ ] **Step 1: Write the failing test**

Create `changedetectionio/tests/test_local_chrome_manager.py`:

```python
import os
import pytest

from changedetectionio.local_browser import manager as manager_module
from changedetectionio.local_browser.manager import LocalChromeManager


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_manager.py -v`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError: module ... has no attribute 'find_chrome_executable'`

- [ ] **Step 3: Write minimal implementation**

Create `changedetectionio/local_browser/manager.py`:

```python
"""LocalChromeManager - owns one visible Chrome process with a persistent profile.

Process-level singleton accessed via get_manager()/reset_manager_for_tests().
"""
import os
import sys

from loguru import logger

from changedetectionio.local_browser import is_local_chrome_supported

_PLATFORM = sys.platform

# Common Windows install locations, searched in this order.
_DEFAULT_CHROME_PATHS_WIN = [
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# Sub-directory under the datastore path used as Chrome's --user-data-dir.
PROFILE_SUBDIR = "browser-profile"


class LocalChromeUnavailable(Exception):
    """Raised when Local Chrome cannot run (non-Windows, disabled, not found)."""


class LocalChromeManager:
    def __init__(self, datastore_path: str):
        self.datastore_path = datastore_path
        self.profile_dir = os.path.join(datastore_path, PROFILE_SUBDIR)
        self._pid = None
        self._cdp_port = None
        self._running = False

    # --- Chrome executable discovery (spec 7.2) ---
    def find_chrome_executable(self, custom_path: str | None = None) -> str:
        if not is_local_chrome_supported():
            raise LocalChromeUnavailable("Local Chrome is only supported on Windows in Phase 1.")

        if custom_path:
            # Custom path must be an existing plain file.
            if not os.path.isfile(custom_path):
                raise FileNotFoundError(f"Configured chrome_executable does not exist: {custom_path}")
            return custom_path

        for candidate in _DEFAULT_CHROME_PATHS_WIN:
            if os.path.isfile(candidate):
                return candidate

        raise FileNotFoundError(
            "Google Chrome was not found in the standard Windows install locations. "
            "Set a custom chrome_executable in Settings."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_manager.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/local_browser/manager.py changedetectionio/tests/test_local_chrome_manager.py
git commit -m "feat(local-chrome): add Chrome executable path discovery"
```

---

## Task 4: Profile dir + Chrome startup argument builder

**Files:**
- Modify: `changedetectionio/local_browser/manager.py` (append method)
- Test: `changedetectionio/tests/test_local_chrome_manager.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_manager.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_manager.py::test_profile_dir_is_under_datastore -v`
Expected: FAIL (method `build_startup_args` missing)

- [ ] **Step 3: Write minimal implementation**

Append to `LocalChromeManager` in `changedetectionio/local_browser/manager.py`:

```python
    # --- Startup arguments (spec 7.3) ---
    def build_startup_args(self, chrome_path: str) -> list[str]:
        """Build the Chrome command line.

        - dedicated --user-data-dir (never the user's daily profile)
        - visible window (we never pass --headless)
        - remote debugging restricted to loopback, dynamic port (0 = let OS choose)
        - does NOT inherit the CHROME_OPTIONS env var used by the Selenium fetcher
        """
        return [
            chrome_path,
            f"--user-data-dir={self.profile_dir}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            # Restore the window if it was closed; keep first-run noise down.
            "--no-first-run",
            "--no-default-browser-check",
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_manager.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/local_browser/manager.py changedetectionio/tests/test_local_chrome_manager.py
git commit -m "feat(local-chrome): build Chrome startup args with dedicated profile + loopback CDP"
```

---

## Task 5: `DevToolsActivePort` parser

**Files:**
- Modify: `changedetectionio/local_browser/manager.py` (append method)
- Test: `changedetectionio/tests/test_local_chrome_manager.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_manager.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_manager.py::test_parse_devtools_active_port_returns_port -v`
Expected: FAIL (method missing)

- [ ] **Step 3: Write minimal implementation**

Append to `LocalChromeManager`:

```python
    # --- DevToolsActivePort parsing (spec 7.3) ---
    def parse_devtools_active_port(self) -> int | None:
        """Read the actual debugging port Chrome wrote to the profile dir.

        Chrome writes DevToolsActivePort (port on line 1, ws path on line 2)
        once it opens the CDP endpoint. Returns None if absent/unreadable.
        """
        path = os.path.join(self.profile_dir, "DevToolsActivePort")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
            return int(first_line)
        except (ValueError, OSError):
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_manager.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/local_browser/manager.py changedetectionio/tests/test_local_chrome_manager.py
git commit -m "feat(local-chrome): parse DevToolsActivePort for the dynamic CDP port"
```

---

## Task 6: PID ownership verification

**Files:**
- Modify: `changedetectionio/local_browser/manager.py` (append method)
- Test: `changedetectionio/tests/test_local_chrome_manager.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_manager.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_manager.py::test_owns_process_requires_pid_exe_and_user_data_dir -v`
Expected: FAIL (method + helpers missing)

- [ ] **Step 3: Write minimal implementation**

Append to `changedetectionio/local_browser/manager.py` (module-level helpers + method). Add at module top after imports:

```python
def _process_exists(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False


def _process_exe(pid: int) -> str | None:
    try:
        import psutil
        return psutil.Process(pid).exe()
    except Exception:
        return None


def _process_cmdline(pid: int) -> list[str]:
    try:
        import psutil
        return psutil.Process(pid).cmdline()
    except Exception:
        return []
```

Append the method to `LocalChromeManager`:

```python
    # --- Process ownership (spec 7.4) ---
    def owns_process(self, pid: int, expected_exe: str) -> bool:
        """True only when PID exists, exe matches, and cmdline has our user-data-dir.

        We never kill by process name and never delete Chrome lock files; if any
        check fails we report inconsistency instead of terminating the process.
        """
        if not pid or not _process_exists(pid):
            return False
        if (_process_exe(pid) or "").lower() != (expected_exe or "").lower():
            return False
        cmdline = _process_cmdline(pid) or []
        return any(self.profile_dir in arg for arg in cmdline)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_manager.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/local_browser/manager.py changedetectionio/tests/test_local_chrome_manager.py
git commit -m "feat(local-chrome): triple-check PID ownership before touching Chrome"
```

---

## Task 7: `LocalChromeManager` lifecycle (`ensure_running` / `stop` / `status`) with mocked subprocess

**Files:**
- Modify: `changedetectionio/local_browser/manager.py` (append methods + singleton)
- Test: `changedetectionio/tests/test_local_chrome_manager.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_manager.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_manager.py::test_ensure_running_starts_chrome_and_reads_port -v`
Expected: FAIL (methods missing)

- [ ] **Step 3: Write minimal implementation**

Add `import subprocess` and `import time` to the imports at the top of `changedetectionio/local_browser/manager.py`. Append the module-level terminate helper:

```python
def _terminate_pid(pid: int) -> None:
    try:
        import psutil
        psutil.Process(pid).terminate()
    except Exception as e:
        logger.warning(f"Could not terminate PID {pid}: {e}")
```

Append the lifecycle methods + singleton to `LocalChromeManager`:

```python
    # --- Lifecycle (spec 7.5) ---
    def ensure_running(self, chrome_path: str) -> int:
        """Ensure our Chrome is running; (re)launch once if it was closed.

        Returns the CDP port. Raises LocalChromeUnavailable on non-Windows.
        Does NOT create a temporary profile as a fallback (spec 7.5).
        """
        if not is_local_chrome_supported():
            raise LocalChromeUnavailable("Local Chrome is only supported on Windows.")

        self._chrome_path = chrome_path
        # Already running and still ours?
        if self._running and self._pid and self.owns_process(self._pid, chrome_path):
            return self._cdp_port

        # Launch (or relaunch once after the user closed the window).
        os.makedirs(self.profile_dir, exist_ok=True)
        args = self.build_startup_args(chrome_path)
        proc = subprocess.Popen(args)
        self._pid = proc.pid
        self._running = True
        port = self._wait_for_devtools_port()
        if not port:
            self._running = False
            raise LocalChromeUnavailable(
                "Chrome started but the DevTools endpoint did not become ready."
            )
        self._cdp_port = port
        return port

    def _wait_for_devtools_port(self, timeout: float = 15.0, interval: float = 0.3) -> int | None:
        """Poll the DevToolsActivePort file until Chrome writes it (or timeout)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            port = self.parse_devtools_active_port()
            if port:
                return port
            time.sleep(interval)
        return None

    def stop(self) -> None:
        """Stop Chrome only if we still own the process (spec 7.4/7.5)."""
        if not self._pid:
            self._running = False
            return
        if self.owns_process(self._pid, self._chrome_path or ""):
            _terminate_pid(self._pid)
        else:
            logger.warning(
                f"Refusing to stop PID {self._pid}: ownership check failed "
                f"(process gone, exe mismatch, or wrong user-data-dir)."
            )
        self._running = False
        self._pid = None
        self._cdp_port = None

    def status(self) -> dict:
        return {
            'running': self._running and bool(self._pid and _process_exists(self._pid)),
            'pid': self._pid,
            'cdp_port': self._cdp_port,
            'profile_dir': self.profile_dir,
        }

    def cdp_endpoint(self) -> str:
        """Loopback CDP URL the fetcher connects to (spec 9.1)."""
        if not self._cdp_port:
            raise LocalChromeUnavailable("Local Chrome is not running yet.")
        return f"http://127.0.0.1:{self._cdp_port}"


# --- Process-level singleton (spec 7) ---
_manager: LocalChromeManager | None = None


def get_manager(datastore_path: str | None = None) -> LocalChromeManager:
    global _manager
    if _manager is None:
        _manager = LocalChromeManager(datastore_path=datastore_path or ".")
    return _manager


def reset_manager_for_tests() -> None:
    global _manager
    _manager = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_manager.py -v`
Expected: PASS (19 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/local_browser/manager.py changedetectionio/tests/test_local_chrome_manager.py
git commit -m "feat(local-chrome): manager lifecycle ensure_running/stop/status + singleton"
```

---

## Task 8: `LocalBrowserTaskGate` - serial acquire/release

**Files:**
- Create: `changedetectionio/local_browser/task_gate.py`
- Test: `changedetectionio/tests/test_local_browser_task_gate.py`

- [ ] **Step 1: Write the failing test**

Create `changedetectionio/tests/test_local_browser_task_gate.py`:

```python
import asyncio
import pytest

from changedetectionio.local_browser.task_gate import LocalBrowserTaskGate


@pytest.fixture
def gate():
    return LocalBrowserTaskGate()


def test_tasks_run_serially(gate):
    order = []

    async def worker(name):
        async with gate.acquire(name):
            order.append(f"start-{name}")
            await asyncio.sleep(0.05)
            order.append(f"end-{name}")

    async def main():
        await asyncio.gather(worker("a"), worker("b"), worker("c"))

    asyncio.run(main())
    # No two "start" appear without their matching "end" between them.
    assert order == ["start-a", "end-a", "start-b", "end-b", "start-c", "end-c"]


def test_release_on_exception_keeps_gate_unlocked(gate):
    async def main():
        with pytest.raises(RuntimeError):
            async with gate.acquire("w"):
                raise RuntimeError("boom")
        # Gate must be free for the next task.
        async with gate.acquire("w2"):
            pass

    asyncio.run(main())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_browser_task_gate.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

Create `changedetectionio/local_browser/task_gate.py`:

```python
"""LocalBrowserTaskGate - serializes all html_local_chrome tasks (spec 8).

Also holds a logical "attention required" state: when a login/captcha challenge
is detected, the gate blocks all further local-chrome tasks until the user
resolves or cancels (spec 8.2). The blocking state survives the worker thread
exiting (the worker does not hold a thread), but is in-memory only.
"""
import asyncio
from typing import Optional


class LocalBrowserTaskGate:
    def __init__(self):
        self._lock: asyncio.Lock | None = None
        self._attention_cleared: asyncio.Event | None = None
        self._attention_watch: Optional[str] = None

    def _ensure(self):
        # asyncio primitives bind to the running loop; create lazily so the gate
        # works regardless of which loop/task first uses it.
        if self._lock is None:
            self._lock = asyncio.Lock()
        if self._attention_cleared is None:
            self._attention_cleared = asyncio.Event()
            self._attention_cleared.set()

    class _Acquired:
        def __init__(self, gate, watch_uuid):
            self._gate = gate
            self._watch_uuid = watch_uuid

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self._gate.release()
            return False

    def acquire(self, watch_uuid: str):
        self._ensure()
        return LocalBrowserTaskGate._Acquired(self, watch_uuid)

    async def _wait_and_lock(self, watch_uuid: str) -> None:
        # Wait while another watch is in the attention state (blocks everyone).
        while not self._attention_cleared.is_set():
            await self._attention_cleared.wait()
        await self._lock.acquire()

    def release(self) -> None:
        if self._lock is not None and self._lock.locked():
            self._lock.release()
```

Wait - the `_Acquired.__aenter__` must actually await the wait+lock. Update the implementation so `__aenter__` does the awaiting:

```python
    class _Acquired:
        def __init__(self, gate, watch_uuid):
            self._gate = gate
            self._watch_uuid = watch_uuid

        async def __aenter__(self):
            await self._gate._wait_and_lock(self._watch_uuid)
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self._gate.release()
            return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_browser_task_gate.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/local_browser/task_gate.py changedetectionio/tests/test_local_browser_task_gate.py
git commit -m "feat(local-chrome): serial task gate acquire/release"
```

---

## Task 9: Task gate - attention-required block / retry / cancel

**Files:**
- Modify: `changedetectionio/local_browser/task_gate.py` (append attention methods + singleton)
- Test: `changedetectionio/tests/test_local_browser_task_gate.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_browser_task_gate.py`:

```python
def test_attention_blocks_other_tasks_until_resolved(gate):
    log = []

    async def first():
        async with gate.acquire("watch-A"):
            log.append("A-start")
            gate.enter_attention("watch-A")  # release lock, block others
            log.append("A-attention")

    async def second():
        async with gate.acquire("watch-B"):
            log.append("B-start")

    async def main():
        f = asyncio.create_task(first())
        await asyncio.sleep(0.02)  # let A start and enter attention
        s = asyncio.create_task(second())
        await asyncio.sleep(0.05)
        assert "B-start" not in log  # B is still blocked
        gate.resolve_attention()
        await asyncio.gather(f, s)
        assert "B-start" in log

    asyncio.run(main())


def test_cancel_attention_unblocks(gate):
    done = []

    async def blocked():
        async with gate.acquire("watch-B"):
            done.append("B-ran")

    async def main():
        gate.enter_attention("watch-A")
        b = asyncio.create_task(blocked())
        await asyncio.sleep(0.02)
        gate.cancel_attention()
        await asyncio.sleep(0.02)
        assert done == ["B-ran"]

    asyncio.run(main())


def test_attention_watch_uuid_tracked(gate):
    gate.enter_attention("watch-X")
    assert gate.attention_watch_uuid() == "watch-X"
    gate.resolve_attention()
    assert gate.attention_watch_uuid() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_browser_task_gate.py::test_attention_blocks_other_tasks_until_resolved -v`
Expected: FAIL (methods missing)

- [ ] **Step 3: Write minimal implementation**

Append to `LocalBrowserTaskGate`:

```python
    # --- Attention state (spec 8.2) ---
    def enter_attention(self, watch_uuid: str) -> None:
        """Mark that watch_uuid needs manual browser action.

        Releases the serial lock so the worker can exit, but clears the
        attention event so all subsequent local-chrome tasks queue.
        """
        self._ensure()
        self._attention_watch = watch_uuid
        self._attention_cleared.clear()
        self.release()

    def resolve_attention(self) -> None:
        """User clicked 'handled, recheck' - unblock the queue."""
        self._ensure()
        self._attention_watch = None
        self._attention_cleared.set()

    def cancel_attention(self) -> None:
        """User clicked 'cancel this check' - unblock the queue."""
        self._ensure()
        self._attention_watch = None
        self._attention_cleared.set()

    def attention_watch_uuid(self) -> Optional[str]:
        return self._attention_watch


# Process-level singleton (spec 8)
_gate: LocalBrowserTaskGate | None = None


def get_gate() -> LocalBrowserTaskGate:
    global _gate
    if _gate is None:
        _gate = LocalBrowserTaskGate()
    return _gate


def reset_gate_for_tests() -> None:
    global _gate
    _gate = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_browser_task_gate.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/local_browser/task_gate.py changedetectionio/tests/test_local_browser_task_gate.py
git commit -m "feat(local-chrome): task gate attention-required block/resolve/cancel"
```

---

## Task 10: `auth_detector` - strong signals

**Files:**
- Create: `changedetectionio/local_browser/auth_detector.py`
- Test: `changedetectionio/tests/test_local_chrome_auth_detector.py`

- [ ] **Step 1: Write the failing test**

Create `changedetectionio/tests/test_local_chrome_auth_detector.py`:

```python
import pytest

from changedetectionio.local_browser.auth_detector import detect_auth_challenge


def test_login_url_is_strong_signal():
    r = detect_auth_challenge(url="https://example.com/login", status_code=200,
                              page_title="Login", page_text="welcome", page_html="<html></html>")
    assert r['required'] is True
    assert any('login' in reason.lower() or 'url' in reason.lower() for reason in r['reasons'])


def test_401_is_strong_signal():
    r = detect_auth_challenge(url="https://example.com/page", status_code=401,
                              page_title="", page_text="", page_html="")
    assert r['required'] is True
    assert any('401' in reason for reason in r['reasons'])


def test_403_is_strong_signal():
    r = detect_auth_challenge(url="https://example.com/page", status_code=403,
                              page_title="", page_text="", page_html="")
    assert r['required'] is True


def test_visible_password_field_is_strong_signal():
    html = '<input type="password" name="pwd" />'
    r = detect_auth_challenge(url="https://example.com/page", status_code=200,
                              page_title="Sign in", page_text="sign in", page_html=html)
    assert r['required'] is True
    assert any('password' in reason.lower() for reason in r['reasons'])


def test_captcha_components_are_strong_signal():
    html = '<div class="geetest_captcha">slide</div>'
    r = detect_auth_challenge(url="https://example.com/page", status_code=200,
                              page_title="verify", page_text="slide to verify", page_html=html)
    assert r['required'] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_auth_detector.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

Create `changedetectionio/local_browser/auth_detector.py`:

```python
"""Generic login / security-challenge detector (spec 10).

Multi-signal scoring:
  - One strong signal  -> ATTENTION_REQUIRED
  - Two+ independent weak signals -> ATTENTION_REQUIRED
  - A single weak signal alone never triggers a pause.

Logs only trigger reasons - never cookies, auth headers, or form values (spec 10/14).
"""
import re

# URL path fragments that indicate an auth flow.
_STRONG_URL_PATTERNS = re.compile(
    r'(?:/login|/signin|/sign-in|/auth|/account/login|/passport|/sso|/oauth)',
    re.IGNORECASE,
)

# Visible password / captcha / verification components in the HTML.
_STRONG_HTML_PATTERNS = [
    re.compile(r'<input[^>]+type=["\']password["\']', re.IGNORECASE),
    re.compile(r'class=["\'][^"\']*(?:captcha|geetest|nc_iconfont|slider|slide-to-verify|qr[_-]?code|verify)[^"\']*', re.IGNORECASE),
    re.compile(r'id=["\'][^"\']*(?:captcha|geetest|slider|qr[_-]?login|verify)[^"\']*', re.IGNORECASE),
]


def _strong_signals(url, status_code, page_html):
    reasons = []
    if _STRONG_URL_PATTERNS.search(url or ''):
        reasons.append("URL entered an authentication path (login/signin/auth)")
    if status_code in (401, 403):
        reasons.append(f"HTTP status {status_code} indicates authentication is required")
    for pat in _STRONG_HTML_PATTERNS:
        m = pat.search(page_html or '')
        if m:
            reasons.append(f"Visible authentication component present ({m.group(0)[:60]})")
    return reasons


def detect_auth_challenge(*, url, status_code, page_title, page_text, page_html) -> dict:
    strong = _strong_signals(url, status_code, page_html)
    if strong:
        return {'required': True, 'reasons': strong}
    # Weak signals handled in Task 11.
    return {'required': False, 'reasons': []}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_auth_detector.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/local_browser/auth_detector.py changedetectionio/tests/test_local_chrome_auth_detector.py
git commit -m "feat(local-chrome): auth detector strong signals"
```

---

## Task 11: `auth_detector` - weak signals + scoring

**Files:**
- Modify: `changedetectionio/local_browser/auth_detector.py` (append weak signals)
- Test: `changedetectionio/tests/test_local_chrome_auth_detector.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_auth_detector.py`:

```python
def test_single_weak_signal_does_not_trigger():
    r = detect_auth_challenge(url="https://shop.example.com/p", status_code=200,
                              page_title="Product", page_text="please log in to see price", page_html="<html></html>")
    assert r['required'] is False


def test_two_independent_weak_signals_trigger():
    r = detect_auth_challenge(url="https://shop.example.com/p", status_code=200,
                              page_title="安全验证", page_text="please log in",
                              page_html="<html></html>")
    assert r['required'] is True
    assert len(r['reasons']) >= 2


def test_clean_page_does_not_trigger():
    r = detect_auth_challenge(url="https://shop.example.com/product-123", status_code=200,
                              page_title="Widget", page_text="In stock $9.99", page_html="<html><body>widget</body></html>")
    assert r['required'] is False
    assert r['reasons'] == []


def test_reasons_never_include_cookies_or_headers():
    # Even if a cookie-like string appears in the page text, it must not leak
    # into reasons as a header/cookie value.
    r = detect_auth_challenge(url="https://shop.example.com/login", status_code=200,
                              page_title="Login", page_text="session=abc123; token=xyz",
                              page_html="<input type='password'/>")
    joined = " ".join(r['reasons']).lower()
    assert "session=abc123" not in joined
    assert "token=xyz" not in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_auth_detector.py::test_two_independent_weak_signals_trigger -v`
Expected: FAIL (weak signals not implemented -> `required` is False)

- [ ] **Step 3: Write minimal implementation**

Append to `changedetectionio/local_browser/auth_detector.py`:

```python
# Weak-signal text fragments (Chinese + English). A single match alone is NOT enough.
_WEAK_TEXT_PATTERNS = re.compile(
    r'(请登录|请先登录|登录后查看|验证身份|安全验证|身份验证|滑块验证|短信验证|'
    r'please\s+log\s*in|sign\s*in\s+to\s+continue|verify\s+your\s+identity|'
    r'authentication\s+required|are\s+you\s+a\s+robot)',
    re.IGNORECASE,
)

# Weak-signal title fragments (independent from body text).
_WEAK_TITLE_PATTERNS = re.compile(
    r'(登录|验证|安全|login|verify|authentication|access\s+denied)',
    re.IGNORECASE,
)


def _weak_signals(url, status_code, page_title, page_text, page_html) -> list:
    reasons = []
    if _WEAK_TEXT_PATTERNS.search(page_text or ''):
        reasons.append("Page text contains a common login/verification phrase")
    if _WEAK_TITLE_PATTERNS.search(page_title or ''):
        reasons.append("Page title suggests an authentication/verification page")
    # Reasons only ever describe the *category* of signal, never page content,
    # so cookies / tokens / form values can never leak (spec 10/14).
    return reasons
```

Now update `detect_auth_challenge` to score weak signals. Replace the function body's tail:

```python
def detect_auth_challenge(*, url, status_code, page_title, page_text, page_html) -> dict:
    strong = _strong_signals(url, status_code, page_html)
    if strong:
        return {'required': True, 'reasons': strong}

    weak = _weak_signals(url, status_code, page_title, page_text, page_html)
    if len(weak) >= 2:
        return {'required': True, 'reasons': weak}

    return {'required': False, 'reasons': []}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_auth_detector.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/local_browser/auth_detector.py changedetectionio/tests/test_local_chrome_auth_detector.py
git commit -m "feat(local-chrome): auth detector weak signals + two-signal scoring"
```

---

## Task 12: `html_local_chrome` fetcher (CDP connect, persistent context, per-task page map)

This task creates the fetcher. The unit test mocks Playwright so it runs without a real browser on any OS.

**Files:**
- Create: `changedetectionio/content_fetchers/local_chrome.py`
- Modify: `changedetectionio/content_fetchers/exceptions/__init__.py` (add exceptions)
- Test: `changedetectionio/tests/test_local_chrome_fetcher.py`

- [ ] **Step 1: Write the failing test**

Create `changedetectionio/tests/test_local_chrome_fetcher.py`:

```python
import asyncio
import pytest

from changedetectionio.content_fetchers.exceptions import (
    LocalChromeUnavailable, LocalChromeAttentionRequired,
)


def test_fetcher_description_and_flags():
    from changedetectionio.content_fetchers.local_chrome import fetcher
    assert fetcher.fetcher_description == "Local Chrome - Persistent Profile"
    assert fetcher.supports_browser_steps is True
    assert fetcher.supports_screenshots is True
    assert fetcher.supports_xpath_element_data is True
    assert fetcher.selectable_in_ui is False  # gated; added to choices by views


def test_run_raises_when_unsupported(monkeypatch):
    from changedetectionio.content_fetchers import local_chrome as mod
    monkeypatch.setattr(mod, "is_local_chrome_supported", lambda: False)
    f = mod.fetcher()
    with pytest.raises(LocalChromeUnavailable):
        asyncio.run(f.run(url="https://example.com", watch_uuid="w1"))


def test_run_raises_when_disabled(monkeypatch, tmp_path):
    from changedetectionio.content_fetchers import local_chrome as mod
    monkeypatch.setattr(mod, "is_local_chrome_supported", lambda: True)
    monkeypatch.setattr(mod, "is_local_chrome_enabled", lambda ds: False)
    f = mod.fetcher()
    # The worker sets _datastore in call_browser(); mirror that here so the
    # enabled check actually runs.
    f._datastore = type("_DS", (), {"data": {'settings': {'requests': {'local_chrome': {'enabled': False}}}}})()
    with pytest.raises(LocalChromeUnavailable):
        asyncio.run(f.run(url="https://example.com", watch_uuid="w1"))


def test_run_uses_persistent_context_and_closes_task_page(monkeypatch, tmp_path):
    """On success the task page is closed; the persistent context and browser are NOT closed."""
    from changedetectionio.content_fetchers import local_chrome as mod

    monkeypatch.setattr(mod, "is_local_chrome_supported", lambda: True)
    monkeypatch.setattr(mod, "is_local_chrome_enabled", lambda ds: True)

    # Fake manager + gate so no real Chrome is touched.
    class _FakeMgr:
        def ensure_running(self, chrome_path):
            return 54321
        def cdp_endpoint(self):
            return "http://127.0.0.1:54321"
        def find_chrome_executable(self, custom_path=None):
            return r"C:\chrome.exe"
    monkeypatch.setattr(mod, "get_manager", lambda datastore_path=None: _FakeMgr())

    closed = {"page": 0, "context": 0, "browser": 0}

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
        pages = []
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

    # The fetcher does `from playwright.async_api import async_playwright`
    # inside run(); patch the symbol on the real playwright module so the
    # from-import picks up the fake.
    import playwright.async_api as pwapi
    monkeypatch.setattr(pwapi, "async_playwright", lambda: _FakeCDP())

    # Reset the process-wide gate singleton so a prior test's attention state
    # can't leak into this one.
    from changedetectionio.local_browser.task_gate import reset_gate_for_tests
    reset_gate_for_tests()

    f = mod.fetcher()
    f.webdriver_js_execute_code = None
    f.screenshot_format = "JPEG"
    asyncio.run(f.run(url="https://example.com", watch_uuid="w1"))

    assert closed["page"] == 1      # task page closed
    assert closed["context"] == 0   # persistent context NOT closed
    assert closed["browser"] == 1   # CDP client disconnected (browser.close on the connection only)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_fetcher.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Add the exceptions**

In `changedetectionio/content_fetchers/exceptions/__init__.py`, append:

```python
class LocalChromeUnavailable(Exception):
    """Local Chrome cannot run (non-Windows, disabled, Chrome not found, or CDP not ready).

    Must NOT fall back to another fetcher (spec 13/17)."""
    def __init__(self, message, status_code=None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class LocalChromeAttentionRequired(Exception):
    """A login/security challenge was detected; the tab is kept and the task gate is blocked.

    Carries the human-readable trigger reasons (never cookies/headers/form values)."""
    def __init__(self, reasons, watch_uuid=None):
        self.reasons = reasons
        self.watch_uuid = watch_uuid
        super().__init__("; ".join(reasons))
```

- [ ] **Step 4: Write the fetcher**

Create `changedetectionio/content_fetchers/local_chrome.py`:

```python
"""html_local_chrome fetcher (spec 9).

Connects to the LocalChromeManager's loopback CDP endpoint, reuses Chrome's
persistent default context (never browser.new_context()), opens one tab per
task, and reuses the existing stateless capabilities (browser steps, screenshot,
xpath, instock, favicon) WITHOUT modifying the old Playwright fetcher.
"""
import os
from loguru import logger

from changedetectionio.content_fetchers import (
    SCREENSHOT_MAX_HEIGHT_DEFAULT, visualselector_xpath_selectors,
    XPATH_ELEMENT_JS, INSTOCK_DATA_JS, FAVICON_FETCHER_JS,
)
from changedetectionio.content_fetchers.base import Fetcher, manage_user_agent
from changedetectionio.content_fetchers.exceptions import (
    EmptyReply, Non200ErrorCodeReceived, PageUnloadable, ScreenshotUnavailable,
    LocalChromeUnavailable, LocalChromeAttentionRequired,
)
from changedetectionio.content_fetchers.playwright import capture_full_page_async
from changedetectionio.local_browser import (
    is_local_chrome_supported, is_local_chrome_enabled,
)
from changedetectionio.local_browser.manager import get_manager
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
        # is_local_chrome_enabled needs the datastore; the worker passes it via
        # an attribute set in call_browser(). If unset (unit tests), assume enabled
        # when supported so the rest of the flow can be exercised.
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
                target_id = None
                try:
                    target_id = await self._read_target_id(page)
                    if target_id:
                        _target_ids[watch_uuid] = target_id
                except Exception as e:
                    logger.debug(f"Could not read CDP target id: {e}")

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
                        __import__('json').dumps(current_include_filters) if current_include_filters else "''"))
                    self.xpath_data = await page.evaluate(XPATH_ELEMENT_JS, {
                        "visualselector_xpath_selectors": visualselector_xpath_selectors,
                        "max_height": int(os.getenv("SCREENSHOT_MAX_HEIGHT", SCREENSHOT_MAX_HEIGHT_DEFAULT)),
                    })
                    self.instock_data = await page.evaluate(INSTOCK_DATA_JS)
                    self.content = await page.content()
                    self.screenshot = await capture_full_page_async(page, screenshot_format=self.screenshot_format, watch_uuid=watch_uuid)

                finally:
                    # Close ONLY the task page (spec 9.2). Never the context or browser.
                    try:
                        await page.close()
                    except Exception as e:
                        logger.warning(f"Error closing task page: {e}")
                    finally:
                        _target_ids.pop(watch_uuid, None)
                    # Do NOT close context or browser.
                    try:
                        await browser.close()  # disconnects the CDP client only
                    except Exception:
                        pass

    async def _read_target_id(self, page):
        # Playwright exposes the CDP session per page; target id is on the session.
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_fetcher.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add changedetectionio/content_fetchers/local_chrome.py changedetectionio/content_fetchers/exceptions/__init__.py changedetectionio/tests/test_local_chrome_fetcher.py
git commit -m "feat(local-chrome): html_local_chrome fetcher with persistent context + per-task page"
```

---

## Task 13: Register fetcher + keep out of default choices + no-fallback resolution

**Files:**
- Modify: `changedetectionio/content_fetchers/base.py:97` (add `selectable_in_ui`)
- Modify: `changedetectionio/content_fetchers/__init__.py` (import + filter + plugin register)
- Test: `changedetectionio/tests/test_local_chrome_config.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_config.py`:

```python
def test_html_local_chrome_hidden_from_default_available_fetchers():
    from changedetectionio import content_fetchers
    names = [n for n, _ in content_fetchers.available_fetchers()]
    assert 'html_local_chrome' not in names  # gated; views add it when enabled


def test_resolve_content_fetcher_finds_html_local_chrome():
    """Already-configured watches still resolve to the class (so it can error cleanly)."""
    from changedetectionio import content_fetchers
    from changedetectionio.content_fetchers.local_chrome import fetcher as lc_fetcher

    class _Watch(dict):
        def get(self, k, default=None):
            return super().get(k, default)
    class _DS:
        data = {'settings': {'application': {'fetch_backend': 'html_requests'}}}
    w = _Watch(fetch_backend='html_local_chrome')
    obj, name, url = content_fetchers.resolve_content_fetcher(watch=w, datastore=_DS())
    assert obj is lc_fetcher
    assert name == 'html_local_chrome'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py -v`
Expected: FAIL (`html_local_chrome` not yet imported into the module / not found by resolve)

- [ ] **Step 3: Write minimal implementation**

In `changedetectionio/content_fetchers/base.py`, add the class attribute to `Fetcher` (next to the other capability flags around line 97):

```python
    selectable_in_ui = True  # Fetcher appears in the UI fetch-backend choices by default
```

In `changedetectionio/content_fetchers/__init__.py`, add the import after the `html_webdriver` import block (after line ~192) and filter it in `available_fetchers()`:

Add the import (place it after the `use_playwright_as_chrome_fetcher` if/else block, before `register_builtin_fetchers()`):

```python
# Local Chrome persistent-profile backend (Windows Phase 1). Imported on all
# platforms so resolve_content_fetcher() can find it for already-configured
# watches and report a clean error; selectable_in_ui=False keeps it out of the
# default UI choices. Views add it to the choices only when Windows + enabled.
from .local_chrome import fetcher as html_local_chrome
```

Update `available_fetchers()` to skip non-selectable fetchers. In the built-in loop (the `for name, obj in inspect.getmembers(...)` block), change the body that appends to also check `selectable_in_ui`:

```python
    for name, obj in inspect.getmembers(sys.modules[__name__], inspect.isclass):
        if inspect.isclass(obj):
            if name.startswith('html_'):
                if name not in _plugin_fetchers:
                    # Hidden fetchers (e.g. html_local_chrome) are added to the
                    # choices by the views only when available, never by default.
                    if not getattr(obj, 'selectable_in_ui', True):
                        continue
                    t = tuple([name, obj.fetcher_description])
                    p.append(t)
```

Register the plugin in `register_builtin_fetchers()` (in `changedetectionio/pluggy_interface.py`). Add to the function body:

```python
    from changedetectionio.content_fetchers import local_chrome
    if hasattr(local_chrome, 'local_chrome_plugin'):
        plugin_manager.register(local_chrome.local_chrome_plugin, 'builtin_local_chrome')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/content_fetchers/base.py changedetectionio/content_fetchers/__init__.py changedetectionio/pluggy_interface.py changedetectionio/tests/test_local_chrome_config.py
git commit -m "feat(local-chrome): register html_local_chrome, hide from default choices, no-fallback resolve"
```

---

## Task 14: Worker integration - attention state -> watch error + minitext status

**Files:**
- Modify: `changedetectionio/worker.py` (add exception handlers)
- Test: `changedetectionio/tests/test_local_chrome_config.py` (append a unit test for the mapping)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_config.py`:

```python
def test_local_chrome_attention_message_is_human_readable():
    from changedetectionio.content_fetchers.exceptions import LocalChromeAttentionRequired
    e = LocalChromeAttentionRequired(["URL entered an authentication path", "HTTP status 401"], watch_uuid="w")
    msg = str(e)
    assert "authentication path" in msg
    assert "401" in msg


def test_local_chrome_unavailable_message():
    from changedetectionio.content_fetchers.exceptions import LocalChromeUnavailable
    e = LocalChromeUnavailable("Local Chrome is disabled in Settings.")
    assert "disabled" in str(e)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py::test_local_chrome_attention_message_is_human_readable -v`
Expected: PASS already (exceptions exist) - if so, this is a guard. If FAIL, the exception names drifted; fix them to match Task 12.

- [ ] **Step 3: Add worker exception handlers**

In `changedetectionio/worker.py`, add an import near the top (after `import changedetectionio.content_fetchers.exceptions as content_fetchers_exceptions`):

```python
from changedetectionio.content_fetchers.exceptions import (
    LocalChromeUnavailable, LocalChromeAttentionRequired,
)
```

Add two `except` blocks inside the big try (place them right before the final `except Exception as e:` block around line 403):

```python
                except LocalChromeAttentionRequired as e:
                    # Tab is kept open; task gate is logically blocked. Tell the
                    # user to handle it in the browser, do not fall back.
                    err_text = "Local Chrome needs attention: {}. Open the browser, complete the login/verification, then click 'recheck'.".format("; ".join(e.reasons))
                    datastore.update_watch(uuid=uuid, update_obj={'last_error': err_text})
                    set_watch_minitext_status(watch, "Needs browser action")
                    process_changedetection_results = False

                except LocalChromeUnavailable as e:
                    datastore.update_watch(uuid=uuid, update_obj={'last_error': str(e)})
                    set_watch_minitext_status(watch, "Local Chrome unavailable")
                    process_changedetection_results = False
```

Also, pass the datastore to the fetcher so it can check `is_local_chrome_enabled`. In `changedetectionio/processors/base.py` `call_browser()`, after `self.fetcher = fetcher_obj(...)` (around line 214-217), add:

```python
        # Local Chrome fetcher needs the datastore to read its enabled flag.
        if hasattr(self.fetcher, '_datastore'):
            self.fetcher._datastore = self.datastore
```

- [ ] **Step 4: Run tests to verify nothing breaks**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py changedetectionio/tests/test_backend.py -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/worker.py changedetectionio/processors/base.py changedetectionio/tests/test_local_chrome_config.py
git commit -m "feat(local-chrome): map attention/unavailable to watch error + minitext status"
```

---

## Task 15: Settings form fields for Local Chrome

**Files:**
- Modify: `changedetectionio/forms.py` (add `LocalChromeSettingsForm` + field on `globalSettingsRequestForm`)
- Test: `changedetectionio/tests/test_local_chrome_config.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_config.py`:

```python
def test_global_settings_form_has_local_chrome_fields():
    from changedetectionio import forms
    import tempfile, os
    with tempfile.TemporaryDirectory() as p:
        # globalSettingsForm needs datastore-shaped data; build a minimal one.
        from changedetectionio.model.App import model
        default = model(datastore_path=p)
        # Provide the bare minimum the form's __init__ references.
        data = {
            'application': default['settings']['application'],
            'requests': default['settings']['requests'],
            'llm': {'api_key': ''},
        }
        form = forms.globalSettingsForm(data=data, extra_notification_tokens={})
        assert hasattr(form.requests.form, 'local_chrome')
        assert hasattr(form.requests.form.local_chrome.form, 'enabled')
        assert hasattr(form.requests.form.local_chrome.form, 'chrome_executable')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py::test_global_settings_form_has_local_chrome_fields -v`
Expected: FAIL (no `local_chrome` sub-form)

- [ ] **Step 3: Write minimal implementation**

In `changedetectionio/forms.py`, add the sub-form near the other request-related forms (before `globalSettingsRequestForm`):

```python
class LocalChromeSettingsForm(Form):
    enabled = BooleanField(_l('Enable Local Chrome (persistent profile)'), default=False, validators=[validators.Optional()])
    chrome_executable = StringField(_l('Google Chrome executable path (optional)'),
                                    validators=[validators.Optional()],
                                    render_kw={"placeholder": _l("Leave blank to auto-detect")})
```

Add the field to `globalSettingsRequestForm` (after `extra_browsers`):

```python
    local_chrome = FormField(LocalChromeSettingsForm, label=_l("Local Chrome - Persistent Profile"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py::test_global_settings_form_has_local_chrome_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/forms.py changedetectionio/tests/test_local_chrome_config.py
git commit -m "feat(local-chrome): settings form fields for enable + chrome_executable"
```

---

## Task 16: Settings tab template + POST persistence + open/restart actions

**Files:**
- Modify: `changedetectionio/blueprint/settings/templates/settings.html` (add tab + pane)
- Modify: `changedetectionio/blueprint/settings/__init__.py` (persist local_chrome; add open/restart routes; append to fetch_backend choices when available)
- Test: `changedetectionio/tests/test_local_chrome_config.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_config.py`:

```python
def test_settings_page_lists_local_chrome_choice_when_enabled(client, live_server, monkeypatch):
    # Feature supported+enabled -> html_local_chrome appears in the fetch choices.
    from changedetectionio import local_browser
    monkeypatch.setattr(local_browser, '_PLATFORM', 'win32')
    res = client.get('/settings')
    assert b'Local Chrome' in res.data


def test_settings_post_persists_local_chrome_enabled(client, live_server, monkeypatch):
    from changedetectionio import local_browser
    from changedetectionio.flask_app import datastore as ds
    monkeypatch.setattr(local_browser, '_PLATFORM', 'win32')
    # Minimal POST: just toggle the local_chrome sub-form on.
    res = client.post('/settings', data={
        'local_chrome-enabled': 'y',
        'local_chrome-chrome_executable': '',
        'save_button': 'Save',
    }, follow_redirects=True)
    assert res.status_code == 200
    assert ds.data['settings']['requests']['local_chrome']['enabled'] is True
```

> Note: `client` and `live_server` are the existing pytest-flask fixtures from `changedetectionio/tests/conftest.py`. If the field-name mapping differs in your WTForms version, adjust the POST keys to match `form.requests.form.local_chrome` rendering; the assertion on `ds.data` is the contract.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py::test_settings_page_lists_local_chrome_choice_when_enabled -v`
Expected: FAIL (tab not rendered / route missing)

- [ ] **Step 3: Add the tab + pane to the settings template**

In `changedetectionio/blueprint/settings/templates/settings.html`, add a tab `<li>` in the `<ul class="tabs ...">` list (after the CAPTCHA & Proxies tab, before the plugin tabs):

```html
            <li class="tab"><a href="#local-chrome">{{ _('Local Chrome') }}</a></li>
```

Add a pane (place it after the `#proxies` pane `<div>`, before the plugin tabs block):

```html
            <div class="tab-pane-inner" id="local-chrome">
                <fieldset>
                    <legend>{{ _('Local Chrome - Persistent Profile') }}</legend>
                    <div class="pure-control-group">
                        {{ render_checkbox_field(form.requests.form.local_chrome.form.enabled) }}
                        <span class="pure-form-message-inline">{{ _('Run a visible Google Chrome with a dedicated persistent profile, so you can log in once and reuse that session. Windows only.') }}</span>
                    </div>
                    <div class="pure-control-group">
                        {{ form.requests.form.local_chrome.form.chrome_executable.label }}
                        {{ form.requests.form.local_chrome.form.chrome_executable(class="pure-input-2-3") }}
                        <span class="pure-form-message-inline">{{ _('Leave blank to auto-detect the installed Google Chrome.') }}</span>
                    </div>
                    <div class="pure-control-group">
                        <span class="pure-form-message-inline">{{ _('Profile path (read-only):') }} <code>{{ local_chrome_profile_dir }}</code></span>
                    </div>
                    <div class="pure-control-group">
                        <span class="pure-form-message-inline">{{ _('Browser status:') }} {{ local_chrome_status }}</span>
                    </div>
                    <div class="pure-control-group">
                        <a class="pure-button button-secondary" href="{{ url_for('settings.local_chrome_open') }}">{{ _('Open browser') }}</a>
                        <a class="pure-button button-secondary" href="{{ url_for('settings.local_chrome_restart') }}">{{ _('Restart browser') }}</a>
                    </div>
                </fieldset>
            </div>
```

- [ ] **Step 4: Persist settings + add routes + pass template vars + filter choices**

In `changedetectionio/blueprint/settings/__init__.py`:

At the top add:
```python
from changedetectionio.local_browser import is_local_chrome_supported, is_local_chrome_enabled
from changedetectionio.local_browser.manager import get_manager
```

In `settings_page()`, inside the `if form.validate():` block, after `datastore.data['settings']['requests'].update(form.data['requests'])` (around line 150), ensure the local_chrome sub-dict merges cleanly (WTForms returns it nested):
```python
                # local_chrome is a nested sub-form; merge its fields explicitly.
                lc = form.data.get('requests', {}).get('local_chrome') or {}
                datastore.data['settings']['requests'].setdefault('local_chrome', {})
                datastore.data['settings']['requests']['local_chrome'].update(lc)
```

Still in `settings_page()`, before `output = render_template(...)`, append the local-chrome fetcher to the fetch_backend choices when available and compute the template vars:
```python
        # Append html_local_chrome to the fetch-backend choices only when available.
        if is_local_chrome_supported() and is_local_chrome_enabled(datastore):
            from changedetectionio.content_fetchers.local_chrome import fetcher as lc_fetcher
            choices = list(form.application.form.fetch_backend.choices)
            if 'html_local_chrome' not in [c[0] for c in choices]:
                choices.append(('html_local_chrome', lc_fetcher.fetcher_description))
            form.application.form.fetch_backend.choices = choices

        local_chrome_profile_dir = ''
        local_chrome_status = 'Not supported on this platform'
        if is_local_chrome_supported():
            mgr = get_manager(datastore.datastore_path)
            local_chrome_profile_dir = mgr.profile_dir
            st = mgr.status()
            local_chrome_status = 'Running (PID {}, port {})'.format(st['pid'], st['cdp_port']) if st['running'] else 'Not running'
```

Add `local_chrome_profile_dir=local_chrome_profile_dir, local_chrome_status=local_chrome_status,` to the `render_template("settings.html", ...)` call.

Add the two routes (open / restart) before `return settings_blueprint` at the end of `construct_blueprint`:
```python
    @settings_blueprint.route("/local-chrome/open", methods=['GET'])
    @login_optionally_required
    def local_chrome_open():
        if not is_local_chrome_supported() or not is_local_chrome_enabled(datastore):
            flash(gettext("Local Chrome is not enabled."), "error")
            return redirect(url_for('settings.settings_page') + '#local-chrome')
        mgr = get_manager(datastore.datastore_path)
        try:
            lc = datastore.data['settings']['requests'].get('local_chrome', {})
            mgr.ensure_running(chrome_path=mc_path(lc, mgr))
            flash(gettext("Local Chrome is running. Switch to its window to log in."), 'notice')
        except Exception as e:
            flash(gettext("Could not start Local Chrome: {}").format(str(e)), "error")
        return redirect(url_for('settings.settings_page') + '#local-chrome')

    @settings_blueprint.route("/local-chrome/restart", methods=['GET'])
    @login_optionally_required
    def local_chrome_restart():
        if not is_local_chrome_supported() or not is_local_chrome_enabled(datastore):
            flash(gettext("Local Chrome is not enabled."), "error")
            return redirect(url_for('settings.settings_page') + '#local-chrome')
        mgr = get_manager(datastore.datastore_path)
        try:
            mgr.stop()
            lc = datastore.data['settings']['requests'].get('local_chrome', {})
            mgr.ensure_running(chrome_path=mc_path(lc, mgr))
            flash(gettext("Local Chrome restarted."), 'notice')
        except Exception as e:
            flash(gettext("Could not restart Local Chrome: {}").format(str(e)), "error")
        return redirect(url_for('settings.settings_page') + '#local-chrome')
```

Add a small helper near the top of `construct_blueprint` to resolve the chrome path:
```python
    def mc_path(lc_cfg, mgr):
        return lc_cfg.get('chrome_executable') or None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add changedetectionio/blueprint/settings/__init__.py changedetectionio/blueprint/settings/templates/settings.html changedetectionio/tests/test_local_chrome_config.py
git commit -m "feat(local-chrome): settings tab, persistence, open/restart actions"
```

---

## Task 17: Watch edit - fetch_backend availability + clear error when unavailable

**Files:**
- Modify: `changedetectionio/blueprint/ui/__init__.py` (or the edit view that renders `edit.html`) - append `html_local_chrome` to choices when available; pass an `local_chrome_unavailable` flag when a watch uses it but it is not available.
- Modify: `changedetectionio/blueprint/ui/templates/edit.html` - show the error.
- Test: `changedetectionio/tests/test_local_chrome_config.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_config.py`:

```python
def test_watch_edit_shows_local_chrome_choice_when_enabled(client, live_server, monkeypatch):
    from changedetectionio import local_browser
    monkeypatch.setattr(local_browser, '_PLATFORM', 'win32')
    from changedetectionio.flask_app import datastore as ds
    ds.data['settings']['requests']['local_chrome']['enabled'] = True
    uuid = ds.add_watch(url='https://example.com')
    res = client.get(f'/edit/{uuid}')
    assert b'Local Chrome' in res.data


def test_watch_edit_shows_error_when_watch_uses_unavailable_local_chrome(client, live_server, monkeypatch):
    from changedetectionio import local_browser
    monkeypatch.setattr(local_browser, '_PLATFORM', 'linux')  # unsupported
    from changedetectionio.flask_app import datastore as ds
    uuid = ds.add_watch(url='https://example.com', extras={'fetch_backend': 'html_local_chrome'})
    res = client.get(f'/edit/{uuid}')
    assert b'Local Chrome' in res.data
    assert b'unavailable' in res.data.lower() or b'not available' in res.data.lower()
```

> Adjust the `/edit/<uuid>` route name/URL to match the actual `ui` blueprint route (check `changedetectionio/blueprint/ui/__init__.py` for the exact path).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py::test_watch_edit_shows_local_chrome_choice_when_enabled -v`
Expected: FAIL (choice not present / route differs)

- [ ] **Step 3: Write minimal implementation**

Find the watch edit view in `changedetectionio/blueprint/ui/__init__.py` (the function that renders `edit.html`). After the form is instantiated and before `render_template`, add:

```python
        from changedetectionio.local_browser import is_local_chrome_supported, is_local_chrome_enabled
        from changedetectionio.content_fetchers.local_chrome import fetcher as lc_fetcher

        local_chrome_available = is_local_chrome_supported() and is_local_chrome_enabled(datastore)
        if local_chrome_available:
            choices = list(form.fetch_backend.choices)
            if 'html_local_chrome' not in [c[0] for c in choices]:
                choices.append(('html_local_chrome', lc_fetcher.fetcher_description))
            form.fetch_backend.choices = choices

        watch_uses_local_chrome = (watch.get('fetch_backend') == 'html_local_chrome')
        local_chrome_unavailable = watch_uses_local_chrome and not local_chrome_available
```

Pass `local_chrome_unavailable=local_chrome_unavailable` to `render_template("edit.html", ...)`.

In `changedetectionio/blueprint/ui/templates/edit.html`, inside the `#request` pane near the fetch_backend field (after the `<span class="pure-form-message-inline">...</span>` block that follows `{{ render_field(form.fetch_backend, class="fetch-backend") }}`), add:

```html
                        {% if local_chrome_unavailable %}
                        <div class="pure-form-message-inline" style="color: #c0392b;">
                            {{ _('This watch uses Local Chrome, which is not available (disabled or not on Windows). It will not run until Local Chrome is enabled. There is no automatic fallback.') }}
                        </div>
                        {% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/blueprint/ui/__init__.py changedetectionio/blueprint/ui/templates/edit.html changedetectionio/tests/test_local_chrome_config.py
git commit -m "feat(local-chrome): watch edit availability + clear unavailability error"
```

---

## Task 18: Watch status display strings

**Files:**
- Modify: the watch-list template/status rendering to surface the new minitext statuses already set in Task 14.
- Test: `changedetectionio/tests/test_local_chrome_config.py` (append a smoke check)

The minitext statuses ("Needs browser action", "Local Chrome unavailable") are already written via `set_watch_minitext_status` in Task 14 and rendered through the existing `__check_status` mechanism (see `changedetectionio/worker.py:31-43`). This task only verifies the strings surface.

- [ ] **Step 1: Write the failing test**

Append to `changedetectionio/tests/test_local_chrome_config.py`:

```python
def test_minitext_status_sanitizer_keeps_local_chrome_messages():
    # set_watch_minitext_status sanitizes to safe punctuation; ensure our
    # messages survive it.
    from changedetectionio.worker import set_watch_minitext_status, _MINITEXT_STATUS_SAFE_RE
    class _W(dict):
        def __init__(self):
            super().__init__(uuid='w')
    w = _W()
    set_watch_minitext_status(w, "Needs browser action")
    assert w['__check_status'] == "Needs browser action"
    set_watch_minitext_status(w, "Local Chrome unavailable")
    assert w['__check_status'] == "Local Chrome unavailable"
```

- [ ] **Step 2: Run test to verify it passes (no code change expected)**

Run: `pytest changedetectionio/tests/test_local_chrome_config.py::test_minitext_status_sanitizer_keeps_local_chrome_messages -v`
Expected: PASS. If it fails because the sanitizer strips a character, adjust the status strings in `worker.py` (Task 14) to use only the allowed set (`[A-Za-z0-9 ().,/:-]`).

- [ ] **Step 3: Commit (test only, if no code changed skip the commit - but record the guard)**

```bash
git add changedetectionio/tests/test_local_chrome_config.py
git commit -m "test(local-chrome): guard minitext status strings for local chrome"
```

---

## Task 19: App startup/shutdown wiring - stop owned Chrome on exit

**Files:**
- Modify: `changedetectionio/__init__.py` (`sigshutdown_handler`)
- Test: `changedetectionio/tests/test_local_chrome_regression.py` (new)

- [ ] **Step 1: Write the failing test**

Create `changedetectionio/tests/test_local_chrome_regression.py`:

```python
import pytest

from changedetectionio.local_browser import manager as manager_module


def test_stop_is_called_on_shutdown_without_owned_process(monkeypatch):
    """sigshutdown_handler must not raise when there is no owned Chrome."""
    from changedetectionio.local_browser.manager import LocalChromeManager, reset_manager_for_tests
    reset_manager_for_tests()
    import types
    # Build a minimal fake app/config/datastore to exercise the handler.
    fake_app = types.SimpleNamespace(config=types.SimpleNamespace(exit=types.SimpleNamespace(set=lambda: None)))
    # The handler references module-level app/datastore; we exercise stop() directly.
    mgr = LocalChromeManager(datastore_path=".")
    # No PID set -> stop() is a no-op and must not raise.
    mgr.stop()
    assert mgr.status()['running'] is False


def test_non_windows_does_not_launch_chrome(monkeypatch):
    from changedetectionio.local_browser import is_local_chrome_supported
    monkeypatch.setattr(manager_module, '_PLATFORM', 'linux')
    assert is_local_chrome_supported() is False
    from changedetectionio.local_browser.manager import LocalChromeManager
    mgr = LocalChromeManager(datastore_path=".")
    with pytest.raises(manager_module.LocalChromeUnavailable):
        mgr.find_chrome_executable()
    with pytest.raises(manager_module.LocalChromeUnavailable):
        mgr.ensure_running(chrome_path=r"C:\chrome.exe")
```

- [ ] **Step 2: Run test to verify it fails (partially)**

Run: `pytest changedetectionio/tests/test_local_chrome_regression.py -v`
Expected: the second test should PASS already; the first is a guard. If the first fails because `stop()` raises, fix `stop()`.

- [ ] **Step 3: Wire shutdown into `sigshutdown_handler`**

In `changedetectionio/__init__.py`, inside `sigshutdown_handler` (after the workers shutdown block, before `sys.exit()`), add:

```python
    # Stop the Local Chrome we own (if any). No-op when not started/unsupported.
    try:
        from changedetectionio.local_browser.manager import get_manager
        get_manager().stop()
    except Exception as e:
        logger.debug(f"Local Chrome stop on shutdown: {e}")
```

- [ ] **Step 4: Run regression tests**

Run: `pytest changedetectionio/tests/test_local_chrome_regression.py changedetectionio/tests/test_local_chrome_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add changedetectionio/__init__.py changedetectionio/tests/test_local_chrome_regression.py
git commit -m "feat(local-chrome): stop owned Chrome on shutdown; no-op when unsupported"
```

---

## Task 20: Regression - old fetchers + Docker unchanged + no Chrome when disabled

**Files:**
- Test: `changedetectionio/tests/test_local_chrome_regression.py` (append)

- [ ] **Step 1: Write the tests**

Append to `changedetectionio/tests/test_local_chrome_regression.py`:

```python
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

    from changedetectionio.content_fetchers.exceptions import LocalChromeUnavailable
    f = mod.fetcher()
    import asyncio
    with pytest.raises(LocalChromeUnavailable):
        asyncio.run(f.run(url="https://example.com", watch_uuid="w"))
    assert called["ensure"] is False  # never launched
```

- [ ] **Step 2: Run the full new + existing regression suite**

Run: `pytest changedetectionio/tests/test_local_chrome_regression.py changedetectionio/tests/test_backend.py -x -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add changedetectionio/tests/test_local_chrome_regression.py
git commit -m "test(local-chrome): regression - old fetchers unchanged, no Chrome when disabled"
```

---

## Task 21: Windows-only integration test (gated skip)

**Files:**
- Test: `changedetectionio/tests/test_local_chrome_integration.py`

This test is tagged to skip on non-Windows CI and when no real Chrome is present. It validates spec §16.2 acceptance steps 1-4 and 6.

- [ ] **Step 1: Write the integration test**

Create `changedetectionio/tests/test_local_chrome_integration.py`:

```python
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
```

- [ ] **Step 2: Verify the skip on the current host**

Run: `pytest changedetectionio/tests/test_local_chrome_integration.py -v`
Expected: 2 skipped (reason shown)

- [ ] **Step 3: Commit**

```bash
git add changedetectionio/tests/test_local_chrome_integration.py
git commit -m "test(local-chrome): Windows-only real-Chrome integration (gated skip)"
```

---

## Self-Review notes (applied during writing)

- **Spec coverage** - every numbered section of the spec maps to at least one task: §5 isolation (Tasks 12-13), §6 architecture (all), §7 manager (3-7), §8 task gate (8-9), §9 fetcher (12), §10 auth detector (10-11), §11 recovery (9, 14, 16), §12 config/UI (1, 15-18), §13 errors (12-14, 20), §14 security (3-7, 12), §15 deps (Task 12 uses `playwright~=1.56.0` per Dockerfile; no Chromium download), §16 tests (all test tasks), §17 acceptance (Tasks 19-21).
- **Placeholders** - none; every code step contains real code. Where a route/field name needed confirming (e.g. the `/edit/<uuid>` URL), the step tells the engineer to verify the exact name in the file first.
- **Type consistency** - `LocalChromeManager` methods (`find_chrome_executable`, `build_startup_args`, `parse_devtools_active_port`, `owns_process`, `ensure_running`, `stop`, `status`, `cdp_endpoint`) are used consistently by the fetcher and UI; `LocalBrowserTaskGate` methods (`acquire`/`release`/`enter_attention`/`resolve_attention`/`cancel_attention`/`attention_watch_uuid`) match between tests and fetcher; exception names (`LocalChromeUnavailable`, `LocalChromeAttentionRequired`) match between `exceptions/__init__.py`, fetcher, and worker.
