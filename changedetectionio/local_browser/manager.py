"""LocalChromeManager - owns one visible Chrome process with a persistent profile.

Process-level singleton accessed via get_manager()/reset_manager_for_tests().
"""
import os
import subprocess
import threading
import time

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


def _terminate_pid(pid: int) -> None:
    try:
        import psutil
        psutil.Process(pid).terminate()
    except Exception as e:
        logger.warning(f"Could not terminate PID {pid}: {e}")


class LocalChromeManager:
    def __init__(self, datastore_path: str):
        self.datastore_path = datastore_path
        self.profile_dir = os.path.join(datastore_path, PROFILE_SUBDIR)
        self._pid = None
        self._cdp_port = None
        self._running = False
        self._chrome_path = None
        # Worker tasks serialize through the gate, but the settings 'Restart'
        # route runs on a Flask thread outside it; this lock keeps the lifecycle
        # atomic against that cross-thread Popen/stop (spec 7.5).
        self._lifecycle_lock = threading.Lock()

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
    def _port_open(self, port: int, timeout: float = 2.0) -> bool:
        """True when something is listening on the loopback CDP port."""
        if not port:
            return False
        import socket
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except OSError:
            return False

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

    # --- Lifecycle (spec 7.5) ---
    def ensure_running(self, chrome_path: str) -> int:
        """Ensure our Chrome is running; (re)launch once if it was closed.

        Returns the CDP port. Raises LocalChromeUnavailable on non-Windows.
        Does NOT create a temporary profile as a fallback (spec 7.5).
        """
        if not is_local_chrome_supported():
            raise LocalChromeUnavailable("Local Chrome is only supported on Windows.")

        with self._lifecycle_lock:
            self._chrome_path = chrome_path
            # Already running and still ours, and its CDP port is actually live?
            if (self._running and self._pid and self.owns_process(self._pid, chrome_path)
                    and self._port_open(self._cdp_port)):
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
            # Ignore a stale DevToolsActivePort file whose port is no longer
            # listening (e.g. a leftover from a killed Chrome instance).
            if port and self._port_open(port):
                return port
            time.sleep(interval)
        return None

    def stop(self) -> None:
        """Stop Chrome only if we still own the process (spec 7.4/7.5)."""
        with self._lifecycle_lock:
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


# Process-level singleton. Intended to be created once at startup; afterwards
# all html_local_chrome tasks are serialized through LocalBrowserTaskGate, and
# the manager's own _lifecycle_lock guards against the settings 'Restart' route
# racing a worker Popen/stop. reset_manager_for_tests() is for test isolation only.
_manager: LocalChromeManager | None = None


def get_manager(datastore_path: str | None = None) -> LocalChromeManager:
    global _manager
    if _manager is None:
        _manager = LocalChromeManager(datastore_path=datastore_path or ".")
    return _manager


def reset_manager_for_tests() -> None:
    global _manager
    _manager = None
