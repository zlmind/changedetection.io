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
            await self._gate._wait_and_lock(self._watch_uuid)
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
