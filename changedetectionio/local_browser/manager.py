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
