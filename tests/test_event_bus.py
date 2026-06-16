import asyncio
import pytest
from src.event_bus import EventBus


@pytest.mark.asyncio
async def test_publish_subscribe():
    eb = EventBus()
    received = []

    async def collector():
        async for event in eb.subscribe("s1"):
            received.append(event)
            if len(received) >= 2:
                break

    task = asyncio.create_task(collector())
    await asyncio.sleep(0.01)
    eb.publish("s1", {"type": "text", "content": "hello"})
    eb.publish("s1", {"type": "text", "content": "world"})
    await task
    assert len(received) == 2
    assert received[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_multiple_subscribers():
    eb = EventBus()
    r1, r2 = [], []

    async def sub(r):
        async for event in eb.subscribe("s1"):
            r.append(event)
            if len(r) >= 1:
                break

    t1 = asyncio.create_task(sub(r1))
    t2 = asyncio.create_task(sub(r2))
    await asyncio.sleep(0.01)
    eb.publish("s1", {"type": "ping"})
    await asyncio.gather(t1, t2)
    assert len(r1) == 1
    assert len(r2) == 1


@pytest.mark.asyncio
async def test_global_publish():
    eb = EventBus()
    received = []

    async def collector():
        async for event in eb.subscribe_global():
            received.append(event)
            break

    task = asyncio.create_task(collector())
    await asyncio.sleep(0.01)
    eb.publish_global("session_started", {"session_id": "abc"})
    await task
    assert received[0]["type"] == "session_started"
