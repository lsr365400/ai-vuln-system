import asyncio
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 1024


class EventBus:
    """In-memory pub/sub per session channel."""

    def __init__(self):
        self._channels: dict[str, list[asyncio.Queue]] = {}

    def publish(self, session_id: str, event: dict) -> None:
        queues = self._channels.get(session_id, [])
        dead = []
        for q in queues:
            try:
                if q.qsize() >= MAX_QUEUE_SIZE:
                    q.get_nowait()  # Drop oldest
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            queues.remove(q)

    async def subscribe(self, session_id: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._channels.setdefault(session_id, []).append(q)
        logger.debug("subscribed to %s (%d listeners)", session_id, len(self._channels[session_id]))
        try:
            while True:
                event = await q.get()
                yield event
        except asyncio.CancelledError:
            pass
        finally:
            queues = self._channels.get(session_id, [])
            if q in queues:
                queues.remove(q)
            if not queues:
                self._channels.pop(session_id, None)
            logger.debug("unsubscribed from %s", session_id)

    def publish_global(self, event_type: str, data: dict) -> None:
        self.publish("__global__", {"type": event_type, **data})

    async def subscribe_global(self) -> AsyncIterator[dict]:
        async for event in self.subscribe("__global__"):
            yield event


bus = EventBus()
