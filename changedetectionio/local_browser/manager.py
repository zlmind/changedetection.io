"""LocalChromeManager - owns one visible Chrome process with a persistent profile.

Process-level singleton accessed via get_manager()/reset_manager_for_tests().
"""
import os

from loguru import logger

from changedetectionio.local_browser import is_local_chrome_supported

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
                raise FileNotFoundError(f"Configured chrome_executable is not an existing file: {custom_path}")
            return custom_path

        for candidate in _DEFAULT_CHROME_PATHS_WIN:
            if os.path.isfile(candidate):
                return candidate

        raise FileNotFoundError(
            "Google Chrome was not found in the standard Windows install locations. "
            "Set a custom chrome_executable in Settings."
        )

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
