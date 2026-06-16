import asyncio
import pytest
from src.scheduler import Scheduler, SessionTask


async def _dummy_run(session_id: str, result: str = "done") -> str:
    await asyncio.sleep(0.05)
    return result


@pytest.mark.asyncio
async def test_enqueue_and_run():
    sched = Scheduler(max_concurrent=2)
    task = SessionTask(
        priority=5, session_id="test-1", project_id="p1",
        target_url="http://x", run_func=_dummy_run,
        args=("test-1",), kwargs={"result": "vuln_found"},
    )
    sched.enqueue(task)
    loop_task = asyncio.create_task(sched.start())
    await asyncio.sleep(0.2)
    status = sched.get_status()
    assert status["running"] == 0  # Task completed
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_priority_ordering():
    sched = Scheduler(max_concurrent=1)
    sched.enqueue(SessionTask(priority=10, session_id="low", project_id="p1",
                               target_url="http://x", run_func=_dummy_run))
    sched.enqueue(SessionTask(priority=1, session_id="high", project_id="p1",
                               target_url="http://x", run_func=_dummy_run))
    assert sched._queue[0].session_id == "high"
    assert sched._queue[1].session_id == "low"


def test_stop_session():
    sched = Scheduler()
    assert not sched.stop_session("nonexistent")
