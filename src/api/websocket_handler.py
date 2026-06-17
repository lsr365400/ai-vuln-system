import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.event_bus import bus

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/sessions/{session_id}/flow")
async def session_flow(ws: WebSocket, session_id: str):
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
    await ws.accept()
    try:
        async for event in bus.subscribe_global():
            await ws.send_text(json.dumps(event, ensure_ascii=False))
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/control")
async def control_ws(ws: WebSocket):
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
