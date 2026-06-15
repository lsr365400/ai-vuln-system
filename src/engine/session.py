import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from src.config import Settings
from src.models import Session, SessionStatus
from src.database import init_db, insert_session, update_session_status
from src.engine.prompt_builder import build_system_prompt
from src.engine.deepseek_client import DeepSeekClient
from src.engine.tool_executor import execute_tool_call

logger = logging.getLogger(__name__)

STATUS_MARKER_PREFIX = "STATUS:"


async def create_session_dir(session_id: str, session_root: Path) -> Path:
    d = session_root / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def detect_status_marker(text: str) -> str | None:
    for line in reversed(text.strip().split("\n")):
        line = line.strip()
        if line.startswith(STATUS_MARKER_PREFIX):
            status = line[len(STATUS_MARKER_PREFIX):].strip()
            if status in ("VULN_FOUND", "LOW_ROI", "NEED_INPUT"):
                return status
    return None


async def run_session(
    settings: Settings,
    target_url: str,
    scenario: str = "custom",
    project_id: str = "default",
    priority: int = 5,
) -> str:
    session_id = str(uuid.uuid4())[:8]
    temp_dir = await create_session_dir(session_id, settings.session_dir)
    report_dir = settings.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    db = await init_db(settings.database_path)

    session = Session(
        id=session_id,
        project_id=project_id,
        scenario=scenario,
        target_url=target_url,
        status=SessionStatus.RUNNING,
        priority=priority,
        temp_dir=temp_dir,
        report_dir=report_dir,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    await insert_session(db, session)

    system_prompt = build_system_prompt(
        core_skill_path=settings.skill_file,
        target_url=target_url,
        scenario=scenario,
        scenarios_dir=Path("scenarios"),
        temp_dir=temp_dir,
        report_dir=report_dir,
    )

    client = DeepSeekClient(settings)
    messages: list[dict] = []
    status_marker = None
    turn_count = 0
    start_time = time.time()
    timeout_seconds = settings.session_timeout_hours * 3600

    try:
        while turn_count < settings.session_max_turns:
            if time.time() - start_time > timeout_seconds:
                logger.warning(f"会话 {session_id} 超时")
                await update_session_status(db, session_id, "error", "硬时间上限")
                return "error"

            if _check_disk_quota(temp_dir, settings.session_disk_limit_gb):
                logger.warning(f"会话 {session_id} 磁盘配额超限")
                await update_session_status(db, session_id, "error", "磁盘配额超限")
                return "error"

            turn_count += 1
            logger.info(f"[{session_id}] Turn {turn_count}/{settings.session_max_turns}")

            try:
                async for event in client.chat_stream(system_prompt, messages):
                    if event["type"] == "text":
                        print(event["content"], end="", flush=True)

                    elif event["type"] == "tool_call":
                        tc = event["tool_call"]
                        logger.info(f"[{session_id}] Tool call: {tc['function']['name']}")

                        result = await execute_tool_call(tc, temp_dir, report_dir)

                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["function"]["name"],
                                    "arguments": tc["function"]["arguments"],
                                },
                            }],
                        })
                        messages.append(result)

                        if tc["function"]["name"] == "finish_session":
                            status_marker = tc["function"].get("arguments_parsed", {}).get("status")
                            logger.info(f"[{session_id}] AI 主动结束: {status_marker}")
                            break

                    elif event["type"] == "finish":
                        pass

            except Exception as e:
                logger.error(f"[{session_id}] API 调用失败: {e}")
                await update_session_status(db, session_id, "error", str(e))
                return "error"

            if status_marker:
                break

            print()

    finally:
        await db.close()

    final_status = _determine_status(status_marker, report_dir, session_id)
    final_status_str = final_status
    await _finalize_session(settings.database_path, session_id, final_status_str)
    return final_status_str


def _check_disk_quota(temp_dir: Path, limit_gb: int) -> bool:
    if not temp_dir.exists():
        return False
    total = sum(f.stat().st_size for f in temp_dir.rglob("*") if f.is_file())
    return total > limit_gb * 1024 * 1024 * 1024


def _determine_status(marker: str | None, report_dir: Path, session_id: str) -> str:
    has_report = False
    for f in report_dir.glob(f"{session_id}*.md"):
        if f.stat().st_size >= 200:
            has_report = True
            break

    if marker == "VULN_FOUND" and has_report:
        return "vuln_found"
    if marker == "VULN_FOUND" and not has_report:
        return "low_roi"
    if marker == "LOW_ROI" and has_report:
        return "vuln_found"
    if marker == "LOW_ROI" and not has_report:
        return "low_roi"
    if marker == "NEED_INPUT":
        return "need_input"
    if marker is None and has_report:
        return "vuln_found"
    if marker is None:
        return "error"
    return "error"


async def _finalize_session(db_path: Path, session_id: str, status: str) -> None:
    db = await aiosqlite.connect(str(db_path))
    await update_session_status(db, session_id, status)
    await db.close()
