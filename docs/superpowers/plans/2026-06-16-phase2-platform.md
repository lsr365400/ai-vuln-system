# Phase 2: 平台化 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 CLI 单会话 MVP 升级为 Web 平台：REST API + WebSocket 实时推送 + 并发调度 + Vue 3 仪表盘

**Architecture:** FastAPI 包装已有 engine/ 模块（零重写），asyncio.Queue 实现内存 EventBus，asyncio.Semaphore 控并发，单文件 Vue 3 CDN 前端。所有组件仍在一个进程内。

**Tech Stack:** FastAPI + uvicorn + asyncio + Vue 3 CDN + Tailwind CSS CDN

---

## 文件映射

| 文件 | 职责 | 行数 |
|------|------|------|
| `src/main.py` | FastAPI 应用入口 + 静态文件挂载 | 40 |
| `src/api/__init__.py` | 包标记 | 1 |
| `src/api/routes_sessions.py` | 会话 CRUD (创建/列表/详情/停止) | 70 |
| `src/api/routes_reports.py` | 报告查询 (列表/详情) | 40 |
| `src/api/websocket_handler.py` | WebSocket 端点 (会话流/仪表盘/控制) | 80 |
| `src/event_bus.py` | 发布/订阅 (asyncio.Queue per channel) | 40 |
| `src/scheduler.py` | 优先级队列 + Semaphore 并发控制 | 80 |
| `src/engine/session.py` | 修改: 接入 EventBus 推送 + scheduler 集成 | +30 |
| `src/database.py` | 修改: 补充查询方法 | +20 |
| `frontend/index.html` | Vue 3 SPA (仪表盘/会话列表/详情/报告/设置) | ~500 |
| `tests/test_event_bus.py` | EventBus 单元测试 | 40 |
| `tests/test_scheduler.py` | 调度器单元测试 | 50 |

**总计新增: ~800 行 Python + ~500 行 HTML**

---

### Task 1: EventBus (发布/订阅)

**Files:**
- Create: `D:\desk\ai测试系统\src\event_bus.py`
- Create: `D:\desk\ai测试系统\tests\test_event_bus.py`

- [ ] **Step 1: 实现 EventBus**

```python
# src/event_bus.py
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
        """Push event to all subscribers of a session channel."""
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
        """Yield events for a session. Auto-cleanup on disconnect."""
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
        """Broadcast to a special 'global' channel for dashboard overview."""
        self.publish("__global__", {"type": event_type, **data})

    async def subscribe_global(self) -> AsyncIterator[dict]:
        """Subscribe to global dashboard events."""
        async for event in self.subscribe("__global__"):
            yield event


# Singleton
bus = EventBus()
```

- [ ] **Step 2: 编写测试**

```python
# tests/test_event_bus.py
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
```

- [ ] **Step 3: 运行测试**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m pytest tests/test_event_bus.py -v`
Expected: 3 PASS

- [ ] **Step 4: Commit**

```bash
git add src/event_bus.py tests/test_event_bus.py
git commit -m "feat: add EventBus — in-memory pub/sub per session channel"
```

---

### Task 2: 并发调度器

**Files:**
- Create: `D:\desk\ai测试系统\src\scheduler.py`
- Create: `D:\desk\ai测试系统\tests\test_scheduler.py`

- [ ] **Step 1: 实现调度器**

```python
# src/scheduler.py
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
    created_at: datetime = field(compare=False)
    session_id: str = field(compare=False)
    project_id: str = field(compare=False)
    target_url: str = field(compare=False)
    run_func: Callable[..., Awaitable[str]] = field(compare=False)
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)


class Scheduler:
    """Priority queue + Semaphore-based concurrency control."""

    def __init__(self, max_concurrent: int = DEFAULT_MAX_CONCURRENT,
                 max_per_project: int = DEFAULT_MAX_PER_PROJECT):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_per_project = max_per_project
        self._queue: list[SessionTask] = []  # Min-heap by priority
        self._running: dict[str, asyncio.Task] = {}
        self._project_counts: dict[str, int] = {}

    def enqueue(self, task: SessionTask) -> None:
        """Add a session to the queue."""
        task.created_at = datetime.now()
        self._queue.append(task)
        self._queue.sort(key=lambda t: (t.priority, t.created_at))
        logger.info("enqueued %s (priority=%d, queue=%d)", task.session_id, task.priority, len(self._queue))

    async def start(self) -> None:
        """Main loop: dequeue tasks when slots are available."""
        while True:
            if self._queue and self._can_start():
                task = self._pop_next()
                if task:
                    asyncio.create_task(self._run_task(task))
            await asyncio.sleep(0.5)

    def _can_start(self) -> bool:
        # Check if there are slots available
        return not self._semaphore.locked() or len(self._running) < self._semaphore._value

    def _pop_next(self) -> SessionTask | None:
        """Pop highest-priority task that won't exceed per-project limit."""
        for i, t in enumerate(self._queue):
            if self._project_counts.get(t.project_id, 0) < self._max_per_project:
                self._queue.pop(i)
                return t
        return None

    async def _run_task(self, task: SessionTask) -> None:
        async with self._semaphore:
            self._running[task.session_id] = asyncio.current_task()  # type: ignore
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
        """Cancel a running session."""
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
```

- [ ] **Step 2: 编写测试**

```python
# tests/test_scheduler.py
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
    # Start scheduler in background
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
    # Low priority first (will be queued behind high)
    sched.enqueue(SessionTask(priority=10, session_id="low", project_id="p1",
                               target_url="http://x", run_func=_dummy_run))
    # High priority later (should run first)
    sched.enqueue(SessionTask(priority=1, session_id="high", project_id="p1",
                               target_url="http://x", run_func=_dummy_run))
    # Queue should be sorted: high (priority=1) first
    assert sched._queue[0].session_id == "high"
    assert sched._queue[1].session_id == "low"


def test_stop_session():
    sched = Scheduler()
    assert not sched.stop_session("nonexistent")
```

- [ ] **Step 3: 运行测试**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m pytest tests/test_scheduler.py -v`
Expected: 3 PASS

- [ ] **Step 4: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: add Scheduler — priority queue + Semaphore concurrency control"
```

---

### Task 3: FastAPI 应用入口 + 数据库改造

**Files:**
- Create: `D:\desk\ai测试系统\src\main.py`
- Create: `D:\desk\ai测试系统\src\api\__init__.py`
- Modify: `D:\desk\ai测试系统\src\database.py` (add query helpers)
- Modify: `D:\desk\ai测试系统\pyproject.toml` (add fastapi/uvicorn deps)

- [ ] **Step 1: 安装依赖**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m pip install fastapi uvicorn aiofiles`

- [ ] **Step 2: 更新 pyproject.toml 依赖**

Add to `[project]` dependencies list in pyproject.toml:
```toml
    "fastapi>=0.115.0",
    "uvicorn>=0.30.0",
    "aiofiles>=24.0.0",
```

- [ ] **Step 3: 数据库查询方法**

Append to `src/database.py`:

```python
async def list_sessions(db: aiosqlite.Connection, limit: int = 50, offset: int = 0,
                        status: str | None = None, project_id: str | None = None) -> list[dict]:
    query = "SELECT * FROM sessions WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


async def get_session(db: aiosqlite.Connection, session_id: str) -> dict | None:
    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


async def list_reports(db: aiosqlite.Connection, limit: int = 50, offset: int = 0,
                       severity: str | None = None) -> list[dict]:
    query = "SELECT * FROM reports WHERE 1=1"
    params = []
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in rows]
```

- [ ] **Step 4: 创建 FastAPI 入口**

```python
# src/main.py
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from src.database import init_db
from src.config import load_settings
from src.event_bus import bus
from src.scheduler import Scheduler
from src.api.routes_sessions import router as sessions_router
from src.api.routes_reports import router as reports_router
from src.api.websocket_handler import router as ws_router

settings = load_settings()
scheduler = Scheduler(max_concurrent=5, max_per_project=3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.db = await init_db(settings.database_path)
    app.state.settings = settings
    app.state.scheduler = scheduler
    app.state.event_bus = bus
    asyncio.create_task(scheduler.start())
    yield
    # Shutdown
    await app.state.db.close()


app = FastAPI(title="AI Vulnerability Scanner", version="2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions_router)
app.include_router(reports_router)
app.include_router(ws_router)

# Serve frontend static files
frontend_dir = Path("frontend")
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
```

- [ ] **Step 5: 验证服务能启动**

Run: `cd "D:/desk/ai测试系统" && timeout 5 "D:/desk/tools/python/py3.14.5/python.exe" -c "
import uvicorn
uvicorn.run('src.main:app', host='0.0.0.0', port=8000, log_level='info')
" 2>&1 || true`

- [ ] **Step 6: Commit**

```bash
git add src/main.py src/api/__init__.py src/database.py pyproject.toml
git commit -m "feat: add FastAPI entry point + database query methods"
```

---

### Task 4: REST API — 会话 + 报告路由

**Files:**
- Create: `D:\desk\ai测试系统\src\api\routes_sessions.py`
- Create: `D:\desk\ai测试系统\src\api\routes_reports.py`

- [ ] **Step 1: 会话路由**

```python
# src/api/routes_sessions.py
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel

from src.models import Session, SessionStatus
from src.database import insert_session, update_session_status, get_session, list_sessions
from src.scheduler import SessionTask
from src.engine.session import run_session

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    target_url: str
    scenario: str = "custom"
    project_id: str = "default"
    priority: int = 5


@router.post("")
async def create_session(req: CreateSessionRequest, request: Request):
    session_id = str(uuid.uuid4())[:8]
    settings = request.app.state.settings
    scheduler = request.app.state.scheduler
    event_bus = request.app.state.event_bus

    # Create session record
    from src.engine.session import create_session_dir
    temp_dir = await create_session_dir(session_id, settings.session_dir)

    session = Session(
        id=session_id,
        project_id=req.project_id,
        scenario=req.scenario,
        target_url=req.target_url,
        status=SessionStatus.QUEUED,
        priority=req.priority,
        temp_dir=temp_dir,
        report_dir=settings.report_dir,
    )
    await insert_session(request.app.state.db, session)

    # Enqueue
    task = SessionTask(
        priority=-req.priority,  # Negate: lower number = higher priority in min-heap
        session_id=session_id,
        project_id=req.project_id,
        target_url=req.target_url,
        run_func=_run_session_wrapper,
        kwargs={
            "settings": settings,
            "target_url": req.target_url,
            "scenario": req.scenario,
            "project_id": req.project_id,
            "priority": req.priority,
            "session_id": session_id,
            "db": request.app.state.db,
            "event_bus": event_bus,
        },
    )
    scheduler.enqueue(task)
    event_bus.publish_global("session_queued", {"session_id": session_id, "target": req.target_url})

    return {"session_id": session_id, "status": "queued"}


async def _run_session_wrapper(settings, target_url, scenario, project_id, priority,
                                session_id, db, event_bus, **kwargs):
    """Wrapper that matches scheduler's callable interface and publishes events."""
    event_bus.publish_global("session_started", {"session_id": session_id})
    # Patch: use pre-assigned session_id
    from src.engine.session import _run_session_with_id
    status = await _run_session_with_id(
        settings=settings, target_url=target_url, scenario=scenario,
        project_id=project_id, priority=priority, session_id=session_id,
        event_bus=event_bus,
    )
    event_bus.publish_global("session_ended", {"session_id": session_id, "status": status})
    return status


@router.get("")
async def list_sessions_api(request: Request, status: str = None, limit: int = 50, offset: int = 0):
    rows = await list_sessions(request.app.state.db, limit=limit, offset=offset, status=status)
    return {"sessions": rows, "total": len(rows)}


@router.get("/{session_id}")
async def get_session_api(session_id: str, request: Request):
    s = await get_session(request.app.state.db, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


@router.post("/{session_id}/stop")
async def stop_session_api(session_id: str, request: Request):
    scheduler = request.app.state.scheduler
    ok = scheduler.stop_session(session_id)
    if ok:
        await update_session_status(request.app.state.db, session_id, "stopped")
    return {"stopped": ok}
```

- [ ] **Step 2: 报告路由**

```python
# src/api/routes_reports.py
from fastapi import APIRouter, Request, Query

from src.database import list_reports

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
async def list_reports_api(request: Request, severity: str = None, limit: int = 50, offset: int = 0):
    rows = await list_reports(request.app.state.db, limit=limit, offset=offset, severity=severity)
    return {"reports": rows, "total": len(rows)}
```

- [ ] **Step 3: 验证 API**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -c "
import uvicorn, asyncio, time
# Start server briefly
proc = None
try:
    proc = asyncio.run(asyncio.create_subprocess_exec(
        'D:/desk/tools/python/py3.14.5/python.exe', '-m', 'uvicorn', 'src.main:app',
        '--host', '0.0.0.0', '--port', '8000',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    ))
    await asyncio.sleep(3)
    print('Server started')
    proc.terminate()
except Exception as e:
    print(f'Error: {e}')
"`

- [ ] **Step 4: Commit**

```bash
git add src/api/routes_sessions.py src/api/routes_reports.py
git commit -m "feat: add REST API — session CRUD + report listing"
```

---

### Task 5: WebSocket 实时推送

**Files:**
- Create: `D:\desk\ai测试系统\src\api\websocket_handler.py`
- Modify: `D:\desk\ai测试系统\src\engine\session.py` (inject event_bus + support pre-assigned session_id)

- [ ] **Step 1: WebSocket 处理器**

```python
# src/api/websocket_handler.py
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.event_bus import bus

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/sessions/{session_id}/flow")
async def session_flow(ws: WebSocket, session_id: str):
    """Stream AI output for a single session."""
    await ws.accept()
    try:
        async for event in bus.subscribe(session_id):
            await ws.send_text(json.dumps(event, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("ws session flow error: %s", e)


@router.websocket("/ws/dashboard/overview")
async def dashboard_overview(ws: WebSocket):
    """Stream global dashboard events."""
    await ws.accept()
    try:
        async for event in bus.subscribe_global():
            await ws.send_text(json.dumps(event, ensure_ascii=False))
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/control")
async def control_ws(ws: WebSocket):
    """Bidirectional control channel."""
    await ws.accept()
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            action = msg.get("action")
            session_id = msg.get("session_id")

            if action == "stop_session":
                bus.publish(session_id, {"type": "control", "action": "stop"})
                await ws.send_text(json.dumps({"ack": "stop_requested", "session_id": session_id}))
            elif action == "ping":
                await ws.send_text(json.dumps({"pong": True}))
    except WebSocketDisconnect:
        pass
```

- [ ] **Step 2: 改造 session.py 支持 event_bus**

Modify `src/engine/session.py` — add a new entry point `_run_session_with_id`:

At the top of `run_session`, extract a shared core:

```python
async def run_session(settings, target_url, scenario="custom", project_id="default", priority=5):
    session_id = str(uuid.uuid4())[:8]
    return await _run_session_with_id(
        settings=settings, target_url=target_url, scenario=scenario,
        project_id=project_id, priority=priority, session_id=session_id,
        event_bus=None,
    )


async def _run_session_with_id(settings, target_url, scenario, project_id,
                                priority, session_id, event_bus=None):
    """Session runner with optional EventBus integration."""
    # ... (same as current run_session body, but use passed session_id)

    # In the main loop, after printing text:
    if event_bus:
        event_bus.publish(session_id, {"type": "text", "content": event["content"]})

    # After tool call execution:
    if event_bus:
        event_bus.publish(session_id, {"type": "tool_call", "name": tc["function"]["name"]})
```

- [ ] **Step 3: Commit**

```bash
git add src/api/websocket_handler.py src/engine/session.py
git commit -m "feat: add WebSocket handlers + EventBus integration in session"
```

---

### Task 6: 前端仪表盘 (Vue 3 SPA)

**Files:**
- Create: `D:\desk\ai测试系统\frontend\index.html`

- [ ] **Step 1: 创建单文件 Vue 3 前端**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI 漏洞挖掘系统</title>
    <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .log-line { font-family: 'Consolas', monospace; font-size: 13px; line-height: 1.5; }
        .log-line:hover { background: #1a2332; }
        .badge-P1 { background: #dc2626; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
        .badge-P2 { background: #ea580c; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
        .badge-P3 { background: #ca8a04; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
        .status-queued { color: #a5d6ff; }
        .status-running { color: #3fb950; animation: pulse 1.5s infinite; }
        .status-vuln_found { color: #f0883e; }
        .status-low_roi { color: #8b949e; }
        .status-error { color: #f85149; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
    </style>
</head>
<body class="bg-gray-950 text-gray-200 min-h-screen">
<div id="app">
    <!-- Navbar -->
    <nav class="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center justify-between">
        <h1 class="text-lg font-bold text-blue-400">AI 漏洞挖掘系统 v2.0</h1>
        <div class="flex gap-4 text-sm">
            <button v-for="tab in tabs" :key="tab.id" @click="currentTab = tab.id"
                    :class="currentTab === tab.id ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400 hover:text-gray-200'"
                    class="px-3 py-1 transition">{{ tab.label }}</button>
        </div>
        <div class="flex items-center gap-3 text-sm">
            <span class="text-gray-400">并发: {{ status.running }}/{{ status.maxConcurrency }}</span>
            <span :class="wsConnected ? 'text-green-400' : 'text-red-400'">●</span>
        </div>
    </nav>

    <!-- Dashboard Tab -->
    <div v-if="currentTab === 'dashboard'" class="p-6">
        <div class="grid grid-cols-4 gap-4 mb-6">
            <div v-for="card in dashboardCards" :key="card.label"
                 class="bg-gray-900 rounded-lg p-4 border border-gray-800">
                <div class="text-gray-400 text-sm">{{ card.label }}</div>
                <div class="text-2xl font-bold mt-1" :class="card.color">{{ card.value }}</div>
            </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
            <div class="bg-gray-900 rounded-lg border border-gray-800 p-4">
                <h3 class="text-sm font-semibold text-gray-400 mb-3">活动会话</h3>
                <div v-if="activeSessions.length === 0" class="text-gray-600 text-sm">暂无</div>
                <div v-for="s in activeSessions" :key="s.id"
                     class="text-sm py-1 border-b border-gray-800 last:border-0 flex justify-between">
                    <span>{{ s.id }} — {{ s.target_url?.substring(0,40) }}</span>
                    <span :class="'status-' + s.status">{{ statusLabel(s.status) }}</span>
                </div>
            </div>
            <div class="bg-gray-900 rounded-lg border border-gray-800 p-4">
                <h3 class="text-sm font-semibold text-gray-400 mb-3">最近漏洞</h3>
                <div v-if="recentVulns.length === 0" class="text-gray-600 text-sm">暂无</div>
                <div v-for="v in recentVulns" :key="v.id"
                     class="text-sm py-1 border-b border-gray-800 last:border-0">
                    <span :class="'badge-' + v.severity">{{ v.severity }}</span>
                    <span class="ml-2">{{ v.title }}</span>
                </div>
            </div>
        </div>
    </div>

    <!-- Sessions Tab -->
    <div v-if="currentTab === 'sessions'" class="p-6">
        <div class="flex gap-4 mb-4">
            <input v-model="newTarget" @keydown.enter="createSession" placeholder="目标 URL"
                   class="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500">
            <select v-model="newScenario" class="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm">
                <option value="custom">custom</option>
                <option value="edu">edu</option>
                <option value="src">src</option>
            </select>
            <button @click="createSession" :disabled="!newTarget"
                    class="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-4 py-2 rounded text-sm font-medium transition">
                创建会话
            </button>
        </div>
        <div class="bg-gray-900 rounded-lg border border-gray-800">
            <div class="grid grid-cols-7 gap-2 px-4 py-2 text-xs text-gray-400 border-b border-gray-800 font-semibold">
                <span>ID</span><span>目标</span><span>场景</span><span>状态</span><span>轮次</span><span>创建时间</span><span>操作</span>
            </div>
            <div v-for="s in sessions" :key="s.id"
                 class="grid grid-cols-7 gap-2 px-4 py-2 text-sm border-b border-gray-800 last:border-0 hover:bg-gray-850 items-center">
                <span class="font-mono text-xs">{{ s.id }}</span>
                <span class="truncate" :title="s.target_url">{{ s.target_url?.substring(0,30) }}</span>
                <span class="text-xs">{{ s.scenario }}</span>
                <span :class="'status-' + s.status">{{ statusLabel(s.status) }}</span>
                <span class="text-xs text-gray-400">{{ s.turn_count || '-' }}</span>
                <span class="text-xs text-gray-400">{{ s.created_at?.substring(0,16) }}</span>
                <span>
                    <button @click="viewSession(s.id)" class="text-blue-400 text-xs hover:underline mr-2">查看</button>
                    <button v-if="s.status==='running'" @click="stopSession(s.id)" class="text-red-400 text-xs hover:underline">停止</button>
                </span>
            </div>
        </div>
        <div class="text-sm text-gray-500 mt-2">共 {{ sessions.length }} 条</div>
    </div>

    <!-- Session Detail Tab -->
    <div v-if="currentTab === 'detail'" class="p-6">
        <button @click="currentTab='sessions'" class="text-blue-400 text-sm mb-4 hover:underline">← 返回列表</button>
        <div class="bg-gray-900 rounded-lg border border-gray-800 p-4 mb-4">
            <h3 class="text-sm font-semibold mb-2">会话 {{ viewingSession?.id }}</h3>
            <div class="text-xs text-gray-400">目标: {{ viewingSession?.target_url }} | 场景: {{ viewingSession?.scenario }}</div>
        </div>
        <div class="bg-black rounded-lg border border-gray-800 p-4 h-96 overflow-y-auto font-mono text-sm" ref="logContainer">
            <div v-for="(line, i) in sessionLog" :key="i" class="log-line py-0.5">
                <span v-if="line.type==='text'" class="text-green-400">{{ line.content }}</span>
                <span v-else-if="line.type==='tool_call'" class="text-yellow-400">[tool: {{ line.name }}]</span>
                <span v-else class="text-gray-500">{{ JSON.stringify(line) }}</span>
            </div>
            <div v-if="sessionLog.length === 0" class="text-gray-600">等待 AI 输出...</div>
        </div>
    </div>

    <!-- Reports Tab -->
    <div v-if="currentTab === 'reports'" class="p-6">
        <div class="flex gap-2 mb-4">
            <button v-for="s in ['', 'P1', 'P2', 'P3']" :key="s"
                    @click="reportFilter = s"
                    :class="reportFilter===s ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400'"
                    class="px-3 py-1 rounded text-xs">{{ s || '全部' }}</button>
        </div>
        <div v-for="r in filteredReports" :key="r.id"
             class="bg-gray-900 rounded-lg border border-gray-800 p-4 mb-3">
            <div class="flex items-center gap-2 mb-2">
                <span :class="'badge-' + r.severity">{{ r.severity }}</span>
                <span class="font-semibold">{{ r.title }}</span>
            </div>
            <div class="text-xs text-gray-400">目标: {{ r.target }} | 类型: {{ r.type }} | {{ r.created_at }}</div>
        </div>
    </div>

    <!-- Settings Tab -->
    <div v-if="currentTab === 'settings'" class="p-6 max-w-lg">
        <div class="bg-gray-900 rounded-lg border border-gray-800 p-6">
            <h3 class="text-sm font-semibold mb-4">系统配置</h3>
            <div class="space-y-4">
                <div>
                    <label class="text-xs text-gray-400">并发上限</label>
                    <input type="number" v-model="status.maxConcurrency" class="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1">
                </div>
                <div>
                    <label class="text-xs text-gray-400">模型</label>
                    <input type="text" value="deepseek-v4-pro" readonly class="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1 text-gray-500">
                </div>
                <div>
                    <label class="text-xs text-gray-400">通知邮箱</label>
                    <input type="text" value="3766177951@qq.com" readonly class="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1 text-gray-500">
                </div>
            </div>
        </div>
    </div>
</div>

<script>
const { createApp, ref, reactive, computed, onMounted, nextTick, watch } = Vue;

createApp({
    setup() {
        const tabs = [
            { id: 'dashboard', label: '仪表盘' },
            { id: 'sessions', label: '会话' },
            { id: 'detail', label: '详情', hide: true },
            { id: 'reports', label: '报告' },
            { id: 'settings', label: '设置' },
        ];
        const currentTab = ref('dashboard');
        const wsConnected = ref(false);
        const status = reactive({ running: 0, queued: 0, maxConcurrency: 5 });
        const sessions = ref([]);
        const activeSessions = computed(() => sessions.value.filter(s => s.status === 'running' || s.status === 'queued'));
        const reports = ref([]);
        const recentVulns = computed(() => reports.value.filter(r => r.severity === 'P1' || r.severity === 'P2').slice(0, 5));
        const reportFilter = ref('');
        const filteredReports = computed(() => reportFilter.value ? reports.value.filter(r => r.severity === reportFilter.value) : reports.value);
        const newTarget = ref('');
        const newScenario = ref('custom');
        const viewingSession = ref(null);
        const sessionLog = ref([]);
        const logContainer = ref(null);

        let dashboardWs = null;
        let sessionWs = null;

        const statusLabel = (s) => ({ queued: '排队中', running: '运行中', vuln_found: '发现漏洞', low_roi: '无发现', need_input: '等待输入', error: '异常', stopped: '已停止' }[s] || s);

        const dashboardCards = computed(() => [
            { label: '运行中', value: status.running, color: 'text-green-400' },
            { label: '排队中', value: status.queued, color: 'text-blue-400' },
            { label: '累计会话', value: sessions.value.length, color: 'text-gray-300' },
            { label: '漏洞报告', value: reports.value.length, color: 'text-orange-400' },
        ]);

        async function loadSessions() {
            const res = await fetch('/api/sessions?limit=100');
            const data = await res.json();
            sessions.value = data.sessions || [];
            status.running = sessions.value.filter(s => s.status === 'running').length;
            status.queued = sessions.value.filter(s => s.status === 'queued').length;
        }

        async function loadReports() {
            const res = await fetch('/api/reports?limit=100');
            const data = await res.json();
            reports.value = data.reports || [];
        }

        async function createSession() {
            if (!newTarget.value) return;
            await fetch('/api/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_url: newTarget.value, scenario: newScenario.value }),
            });
            newTarget.value = '';
            await loadSessions();
        }

        async function stopSession(id) {
            await fetch('/api/sessions/' + id + '/stop', { method: 'POST' });
            await loadSessions();
        }

        function viewSession(id) {
            viewingSession.value = sessions.value.find(s => s.id === id);
            sessionLog.value = [];
            currentTab.value = 'detail';
            // Connect to session WebSocket
            if (sessionWs) sessionWs.close();
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            sessionWs = new WebSocket(proto + '//' + location.host + '/ws/sessions/' + id + '/flow');
            sessionWs.onmessage = (e) => {
                sessionLog.value.push(JSON.parse(e.data));
                nextTick(() => {
                    if (logContainer.value) logContainer.value.scrollTop = logContainer.value.scrollHeight;
                });
            };
            sessionWs.onerror = () => { console.log('WS error'); };
        }

        function connectDashboard() {
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            dashboardWs = new WebSocket(proto + '//' + location.host + '/ws/dashboard/overview');
            dashboardWs.onopen = () => { wsConnected.value = true; };
            dashboardWs.onclose = () => { wsConnected.value = false; setTimeout(connectDashboard, 3000); };
            dashboardWs.onmessage = (e) => {
                const event = JSON.parse(e.data);
                if (event.type === 'session_started' || event.type === 'session_ended' || event.type === 'session_queued') {
                    loadSessions();
                    loadReports();
                }
            };
        }

        onMounted(() => {
            loadSessions();
            loadReports();
            connectDashboard();
        });

        return { tabs, currentTab, wsConnected, status, sessions, activeSessions, reports, recentVulns,
                 reportFilter, filteredReports, newTarget, newScenario, viewingSession, sessionLog, logContainer,
                 dashboardCards, statusLabel, createSession, stopSession, viewSession, loadSessions };
    }
}).mount('#app');
</script>
</body>
</html>
```

- [ ] **Step 2: 验证前端可用**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -c "from pathlib import Path; assert Path('frontend/index.html').exists(); print('Frontend file OK:', Path('frontend/index.html').stat().st_size, 'bytes')"`

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add Vue 3 dashboard SPA (5 tabs, WebSocket real-time)"
```

---

### Task 7: 集成测试 + 联调

**Files:**
- Run all tests

- [ ] **Step 1: 运行全部单元测试**

```bash
cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m pytest tests/ -v --tb=short
```
Expected: All tests pass (28+ tests: 25 existing + 3 event_bus + 3 scheduler)

- [ ] **Step 2: 启动完整服务**

```bash
cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m uvicorn src.main:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 3: 浏览器验证**

Open http://localhost:8000 — verify:
- Dashboard loads (仪表盘)
- Sessions list shows history
- Reports tab shows past reports
- Create new session from UI works
- Session detail shows real-time AI output

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "chore: Phase 2 integration — all tests pass, service starts"
```

---

### Task 8: 部署到服务器

- [ ] **Step 1: 同步代码到 43.136.92.170**

```bash
rsync -avz --exclude '.git' --exclude '__pycache__' --exclude 'data/' \
  "D:/desk/ai测试系统/" ubuntu@43.136.92.170:~/ai-vuln-system/
```

- [ ] **Step 2: 服务器上安装依赖并启动**

```bash
ssh ubuntu@43.136.92.170
cd ~/ai-vuln-system
pip install -e ".[dev]" fastapi uvicorn aiofiles
# 配置 .env
nohup python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 &
```

- [ ] **Step 3: 验证远程访问**

Open http://43.136.92.170:8000 — verify dashboard loads

---

## 验证清单

- [ ] 28+ tests pass
- [ ] `uvicorn src.main:app` starts without errors
- [ ] POST /api/sessions creates and queues a session
- [ ] GET /api/sessions returns session list
- [ ] GET /api/reports returns reports from DB
- [ ] WebSocket /ws/dashboard/overview broadcasts events
- [ ] WebSocket /ws/sessions/{id}/flow streams AI output
- [ ] Dashboard SPA loads and shows data
- [ ] Creating a session from UI triggers AI test
- [ ] Real-time log appears in session detail
