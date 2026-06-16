import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = 5
DEFAULT_MAX_PER_PROJECT = 3


@dataclass(order=True)
class SessionTask:
    priority: int
    session_id: str = field(compare=False)
    project_id: str = field(compare=False)
    target_url: str = field(compare=False)
    run_func: Callable[..., Awaitable[str]] = field(compare=False)
    created_at: datetime | None = field(default=None, compare=False)
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)


class Scheduler:
    """Priority queue + Semaphore-based concurrency control."""

    def __init__(self, max_concurrent: int = DEFAULT_MAX_CONCURRENT,
                 max_per_project: int = DEFAULT_MAX_PER_PROJECT):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_per_project = max_per_project
        self._queue: list[SessionTask] = []
        self._running: dict[str, asyncio.Task] = {}
        self._project_counts: dict[str, int] = {}

    def enqueue(self, task: SessionTask) -> None:
        task.created_at = datetime.now()
        self._queue.append(task)
        self._queue.sort(key=lambda t: (t.priority, t.created_at))
        logger.info("enqueued %s (priority=%d, queue=%d)", task.session_id, task.priority, len(self._queue))

    async def start(self) -> None:
        while True:
            if self._queue and self._can_start():
                task = self._pop_next()
                if task:
                    asyncio.create_task(self._run_task(task))
            await asyncio.sleep(0.5)

    def _can_start(self) -> bool:
        return not self._semaphore.locked() or len(self._running) < self._semaphore._value

    def _pop_next(self) -> SessionTask | None:
        for i, t in enumerate(self._queue):
            if self._project_counts.get(t.project_id, 0) < self._max_per_project:
                self._queue.pop(i)
                return t
        return None

    async def _run_task(self, task: SessionTask) -> None:
        async with self._semaphore:
            self._running[task.session_id] = asyncio.current_task()
            self._project_counts[task.project_id] = self._project_counts.get(task.project_id, 0) + 1
            logger.info("started %s (running=%d, project=%s)", task.session_id, len(self._running), task.project_id)
            try:
                await task.run_func(*task.args, **task.kwargs)
            except Exception as e:
                logger.error("session %s crashed: %s", task.session_id, e)
            finally:
                self._running.pop(task.session_id, None)
                self._project_counts[task.project_id] = max(0, self._project_counts.get(task.project_id, 0) - 1)
                logger.info("finished %s (running=%d)", task.session_id, len(self._running))

    def stop_session(self, session_id: str) -> bool:
        t = self._running.get(session_id)
        if t:
            t.cancel()
            return True
        return False

    def get_status(self) -> dict:
        return {
            "running": len(self._running),
            "queued": len(self._queue),
            "session_ids": list(self._running.keys()),
            "by_project": dict(self._project_counts),
        }
