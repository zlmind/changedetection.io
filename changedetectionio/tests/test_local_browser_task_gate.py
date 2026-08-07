import asyncio

import pytest

from changedetectionio.local_browser.task_gate import LocalBrowserTaskGate


@pytest.fixture(autouse=True)
def prepare_test_function():
    """Override conftest's live_server-dependent autouse fixture for pure unit tests."""
    yield


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


def test_waiter_queued_before_attention_does_not_slip_through(gate):
    """A waiter already queued on the lock when enter_attention fires must NOT
    run until resolve_attention - it must re-check the attention event after
    acquiring the lock."""
    log = []

    async def holder():
        async with gate.acquire("watch-A"):
            log.append("A-start")
            await asyncio.sleep(0.05)  # let B queue on the lock while A holds it
            gate.enter_attention("watch-A")  # clear event; A still holds lock
            log.append("A-attention")
            # exiting the async with releases A's lock via __aexit__

    async def waiter():
        async with gate.acquire("watch-B"):
            log.append("B-start")

    async def main():
        a = asyncio.create_task(holder())
        await asyncio.sleep(0.02)  # let A acquire and hold
        b = asyncio.create_task(waiter())  # B queues on the lock (event still set)
        await asyncio.sleep(0.05)
        # B must NOT have run - attention was entered while B was queued.
        assert "B-start" not in log
        gate.resolve_attention()
        await asyncio.gather(a, b)
        assert "B-start" in log

    asyncio.run(main())
