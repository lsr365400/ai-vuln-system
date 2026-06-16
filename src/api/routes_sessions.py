import uuid
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel

from src.models import Session, SessionStatus
from src.database import insert_session, update_session_status, get_session, list_sessions, get_event_log
from src.scheduler import SessionTask

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

    task = SessionTask(
        priority=-req.priority,
        session_id=session_id,
        project_id=req.project_id,
        target_url=req.target_url,
        run_func=_run_session_wrapper,
        kwargs={
            "settings": settings,
            "target_url": req.target_url,
            "scenario": req.scenario,
            "project_id": req.project_id,
            "session_id": session_id,
            "db": request.app.state.db,
            "event_bus": event_bus,
        },
    )
    scheduler.enqueue(task)
    event_bus.publish_global("session_queued", {"session_id": session_id, "target": req.target_url})

    return {"session_id": session_id, "status": "queued"}


async def _run_session_wrapper(settings, target_url, scenario, project_id, session_id, db, event_bus, **kwargs):
    event_bus.publish_global("session_started", {"session_id": session_id})
    from src.engine.session import _run_session_with_id
    status = await _run_session_with_id(
        settings=settings, target_url=target_url, scenario=scenario,
        project_id=project_id, session_id=session_id,
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


@router.get("/{session_id}/events")
async def get_session_events(session_id: str, request: Request, limit: int = 500):
    events = await get_event_log(request.app.state.db, session_id, limit=limit)
    return {"events": events, "total": len(events)}


@router.post("/{session_id}/stop")
async def stop_session_api(session_id: str, request: Request):
    scheduler = request.app.state.scheduler
    ok = scheduler.stop_session(session_id)
    if ok:
        await update_session_status(request.app.state.db, session_id, "stopped")
    return {"stopped": ok}
