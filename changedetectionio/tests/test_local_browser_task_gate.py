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
