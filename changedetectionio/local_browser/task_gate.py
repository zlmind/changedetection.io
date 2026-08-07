"""LocalBrowserTaskGate - serializes all html_local_chrome tasks (spec 8).

Also holds a logical "attention required" state: when a login/captcha challenge
is detected, the gate blocks all further local-chrome tasks until the user
resolves or cancels (spec 8.2). The blocking state survives the worker thread
exiting (the worker does not hold a thread), but is in-memory only.

Thread-safety: worker_pool runs each worker in its OWN thread with its own
event loop, so an asyncio.Lock bound to one loop would crash on the next.
Mutual exclusion is therefore a process-wide `threading.Lock` (acquired from
whatever thread/loop is calling); waiting for the lock/attention uses a short
async sleep loop so no loop-bound primitives leak across threads.
"""
import asyncio
import threading
from typing import Optional

# Polling interval while waiting for the serial lock / attention to clear.
# The gate is only ever held for the duration of one browser task, so a short
# poll keeps wakeups cheap while remaining loop-agnostic.
_POLL_INTERVAL = 0.01


class LocalBrowserTaskGate:
    def __init__(self):
        self._thread_lock = threading.Lock()
        self._attention_watch: Optional[str] = None
        self._lock_held = False

    def _acquire_thread_lock(self):
        # Blocking acquire in a worker thread is fine: the holder is another
        # worker thread, not this one's loop.
        self._thread_lock.acquire()

    def _release_thread_lock(self):
        if self._thread_lock.locked():
            self._thread_lock.release()

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
        return LocalBrowserTaskGate._Acquired(self, watch_uuid)

    async def _wait_and_lock(self, watch_uuid: str) -> None:
        while True:
            # Block while some watch is in the attention state (blocks everyone).
            while self._attention_watch is not None:
                await asyncio.sleep(_POLL_INTERVAL)
            # Try to take the serial lock; if a concurrent worker owns it,
            # yield and retry. enter_attention may fire while we wait - the
            # loop re-checks _attention_watch before granting the lock.
            if self._thread_lock.acquire(blocking=False):
                try:
                    if self._attention_watch is None:
                        self._lock_held = True
                        return
                finally:
                    if not self._lock_held:
                        self._thread_lock.release()
            await asyncio.sleep(_POLL_INTERVAL)

    def release(self) -> None:
        if self._lock_held:
            self._lock_held = False
            self._release_thread_lock()

    # --- Attention state (spec 8.2) ---
    def enter_attention(self, watch_uuid: str) -> None:
        """Mark that watch_uuid needs manual browser action.

        Blocks all subsequent local-chrome tasks until resolve/cancel. Does NOT
        release the serial lock here - the holding worker releases it via
        __aexit__ when it raises and exits the `async with` block.
        """
        self._attention_watch = watch_uuid

    def resolve_attention(self) -> None:
        """User clicked 'handled, recheck' - unblock the queue."""
        self._attention_watch = None

    def cancel_attention(self) -> None:
        """User clicked 'cancel this check' - unblock the queue."""
        self._attention_watch = None

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
