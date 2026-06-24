import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from src.config import Settings
from src.models import Session, SessionStatus
from src.database import init_db, insert_session, update_session_status, insert_event_log, track_endpoint, get_tested_endpoints, get_tested_urls, track_failed_path, get_failed_paths, get_effective_techniques, record_technique, generalize_tech_stack
from src.engine.prompt_builder import build_system_prompt
from src.engine.deepseek_client import DeepSeekClient
from src.engine.tool_executor import execute_tool_call
from src.engine.memory.hermes_store import HermesStore
from src.engine.memory.compressor import should_compress, compress_messages, estimate_tokens
from src.engine.memory.pentagi_memory import build_cross_target_context, store_vector, record_attack_chain
from src.engine.report_indexer import index_all_reports
from src.safety.disk_guard import DiskGuard
from src.engine.browser_tool import cleanup_context

logger = logging.getLogger(__name__)

STATUS_MARKER_PREFIX = "STATUS:"
COMPRESS_EVERY_N_TURNS = 10  # Check compression need every N turns
MEMORY_DIR = Path("data/memory")


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
    """Legacy entry point: generates session_id and delegates to _run_session_with_id."""
    session_id = str(uuid.uuid4())[:8]
    return await _run_session_with_id(
        settings=settings, target_url=target_url, scenario=scenario,
        project_id=project_id, priority=priority, session_id=session_id,
        event_bus=None,
    )


async def _run_session_with_id(
    settings: Settings,
    target_url: str,
    scenario: str = "custom",
    project_id: str = "default",
    priority: int = 5,
    session_id: str = "",
    event_bus=None,
    user_input: str = "",
) -> str:
    """Core session runner that accepts a pre-assigned session_id and optional EventBus."""
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
    try:
        await insert_session(db, session)
    except Exception:
        # Session may already exist (e.g. scheduler path), update status to running
        await db.execute(
            "UPDATE sessions SET status='running', started_at=datetime('now') WHERE id=?",
            (session_id,),
        )
        await db.commit()

    # ------------------------------------------------------------------
    # Load long-term memory and inject into system prompt
    # ------------------------------------------------------------------
    memory_store = HermesStore(MEMORY_DIR)
    memory_context = memory_store.build_memory_context(target_url)

    system_prompt = build_system_prompt(
        core_skill_path=settings.skill_file,
        target_url=target_url,
        scenario=scenario,
        scenarios_dir=Path("scenarios"),
        temp_dir=temp_dir,
        report_dir=report_dir,
    )

    if user_input:
        system_prompt += f"\n\n## 用户指令\n\n用户说：{user_input}\n\n根据用户指引继续测试。"

    if memory_context:
        memory_header = (
            "\n\n## 长期记忆（来自之前的会话）\n\n"
            "以下是对同一目标的过往测试记录。已测过的方向不要重复，已确认的漏洞不要重复报告。\n\n"
        )
        system_prompt += memory_header + memory_context
        logger.info("[%s] 注入了长期记忆 (%d chars)", session_id, len(memory_context))

    # Inject failed paths only (not every tested URL — too noisy)
    from urllib.parse import urlparse
    host = urlparse(target_url).netloc or target_url

    failed = await get_failed_paths(db, host)
    if failed:
        failed_text = "\n".join(
            f"- **{f['technique']}**: {f['reason']}"
            for f in failed[:10]
        )
        system_prompt += (
            "\n\n## ⛔ 已确认无效的攻击路径（优先阅读，不要重复！）\n\n"
            + failed_text + "\n"
        )

    # Cross-target experience: query effective techniques for similar tech stacks
    # Use basic recon hints — exact tech stack comes from the AI during testing
    tech_hints = ["RuoYi", "SpringBoot", "SafeLine", "nginx", "Flask", "Django",
                  "PHP", "Java", "Python", "Laravel", "ThinkPHP", "Tomcat"]
    effective = await get_effective_techniques(db, tech_hints)
    if effective:
        success_lines = "\n".join(
            f"- ✅ **{e['technique']}** → {e['outcome']} (已验证 {e['count']} 次, 技术栈: {e['tech_stack']})"
            for e in effective[:8]
        )
        system_prompt += (
            "\n\n## 🧠 跨目标经验（已验证有效的技术，优先尝试）\n\n"
            + success_lines + "\n"
        )

    # PentAGI vector memory: semantic search for similar past findings
    try:
        vector_ctx = await build_cross_target_context(db, target_url)
        if vector_ctx:
            system_prompt += "\n\n" + vector_ctx
            logger.info("[%s] vector memory injected", session_id)
    except Exception as e:
        logger.debug("[%s] vector memory skipped: %s", session_id, e)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    disk_guard = DiskGuard(temp_dir, settings.session_disk_limit_gb)
    client = DeepSeekClient(settings)
    messages: list[dict] = []
    status_marker = None
    final_status = None
    turn_count = 0
    accumulated_text = ""  # Full AI output for marker detection
    start_time = time.time()
    timeout_seconds = settings.session_timeout_hours * 3600

    try:
        while turn_count < settings.session_max_turns:
            if time.time() - start_time > timeout_seconds:
                logger.warning(f"会话 {session_id} 超时")
                await update_session_status(db, session_id, "error", "硬时间上限")
                status_marker = None
                final_status = "error"
                break

            over, usage = disk_guard.check_quota()
            if over:
                logger.warning("会话 %s 磁盘配额超限: %d bytes", session_id, usage)
                await update_session_status(db, session_id, "error", "磁盘配额超限")
                status_marker = None
                final_status = "error"
                break

            turn_count += 1
            logger.info(f"[{session_id}] Turn {turn_count}/{settings.session_max_turns}")

            # ------------------------------------------------------------------
            # Periodic compression check
            # ------------------------------------------------------------------
            if turn_count > 1 and turn_count % COMPRESS_EVERY_N_TURNS == 0:
                need, level = should_compress(messages)
                if need:
                    logger.info(
                        "[%s] 触发压缩 (level=%s, est_tokens=%d)",
                        session_id, level, estimate_tokens(messages),
                    )
                    await compress_messages(client, messages, keep_last=12)
                    logger.info("[%s] 压缩完成 (%d messages remain)", session_id, len(messages))

            text_buffer = ""  # Buffer text chunks into sentences before DB write
            try:
                async for event in client.chat_stream(system_prompt, messages):
                    if event["type"] == "text":
                        print(event["content"], end="", flush=True)
                        accumulated_text += event["content"]
                        text_buffer += event["content"]
                        if event_bus:
                            event_bus.publish(session_id, {"type": "text", "content": event["content"]})

                    elif event["type"] == "tool_call":
                        # Flush buffered text as one event
                        if text_buffer.strip():
                            await insert_event_log(db, session_id, "text", text_buffer.strip())
                            text_buffer = ""
                        tc = event["tool_call"]
                        logger.info(f"[{session_id}] Tool call: {tc['function']['name']}")
                        if event_bus:
                            event_bus.publish(session_id, {"type": "tool_call", "name": tc["function"]["name"]})
                        await insert_event_log(db, session_id, "tool_call", tc["function"]["name"])

                        try:
                            result = await execute_tool_call(tc, temp_dir, report_dir, session_id=session_id)
                        except Exception as tool_err:
                            logger.error("[%s] Tool %s crashed: %s", session_id, tc["function"]["name"], tool_err)
                            result = {"tool_call_id": tc["id"], "role": "tool",
                                       "content": f"Tool execution failed: {tool_err} — try a different approach"}

                        # Auto-detect login attempts and inject auth status
                        if tc["function"]["name"] == "curl_http":
                            args = tc["function"].get("arguments_parsed", {})
                            body = str(args.get("body", "")).lower()
                            url = str(args.get("url", ""))
                            method = str(args.get("method", "GET")).upper()
                            cookies = str(args.get("headers", {}).get("Cookie", ""))
                            is_login = (method == "POST" and any(kw in url.lower() + body
                                for kw in ["login", "signin", "登录", "username", "password"]))
                            if is_login and cookies:
                                from urllib.parse import urlparse
                                from src.engine.tool_executor import execute_check_auth
                                base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                                auth_result = await execute_check_auth({
                                    "target_base": base, "cookies": cookies,
                                })
                                result["content"] += f"\n[AUTO] 登录检测: authenticated={auth_result['authenticated']}, {auth_result['evidence']}"

                        # Track endpoints for curl/discover calls (skip static assets)
                        if tc["function"]["name"] in ("curl_http", "discover_endpoints"):
                            args = tc["function"].get("arguments_parsed", {})
                            tracked_url = args.get("url", "")
                            if tracked_url and not any(tracked_url.endswith(ext) for ext in
                                    (".js", ".css", ".png", ".jpg", ".ico", ".svg", ".woff", ".ttf")):
                                tracked_method = args.get("method", "GET") if tc["function"]["name"] == "curl_http" else "GET"
                                await track_endpoint(db, session_id, tracked_url, tracked_method)
                            # Auto-track failures (403=WAF, 500=error, empty=dead end)
                            result_str = str(result.get("content", ""))
                            status = result.get("status_code", 0) if isinstance(result, dict) else 0
                            if status == 403:
                                await track_failed_path(db, session_id, tracked_url, "WAF拦截(403)", "SafeLine封杀")
                            elif status == 429:
                                await track_failed_path(db, session_id, tracked_url, "频率限制(429)", "rate-limited")
                            elif "error" in result_str.lower() and "timeout" not in result_str.lower():
                                await track_failed_path(db, session_id, tracked_url, "请求失败", result_str[:150])

                        assistant_msg: dict = {
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
                        }
                        # Pass back reasoning_content for DeepSeek thinking mode
                        rc = event.get("reasoning_content", "")
                        if rc:
                            assistant_msg["reasoning_content"] = rc
                        messages.append(assistant_msg)
                        messages.append(result)

                        if tc["function"]["name"] == "finish_session":
                            status_marker = tc["function"].get("arguments_parsed", {}).get("status")
                            logger.info(f"[{session_id}] AI 主动结束: {status_marker}")
                            break

                    elif event["type"] == "finish":
                        if text_buffer.strip():
                            await insert_event_log(db, session_id, "text", text_buffer.strip())
                            text_buffer = ""

            except Exception as e:
                logger.error(f"[{session_id}] API 调用失败: {e}")
                await update_session_status(db, session_id, "error", str(e))
                status_marker = None
                final_status = "error"
                break

            if status_marker:
                break

            # Also check accumulated_text for status marker
            marker_from_text = detect_status_marker(accumulated_text)
            if marker_from_text:
                status_marker = marker_from_text
                break

            print()

    finally:
        pass  # db handled below

    # ------------------------------------------------------------------
    # Index reports to SQLite + email notify
    # ------------------------------------------------------------------
    try:
        indexed = await index_all_reports(db, session_id, report_dir)
        if indexed:
            p1p2 = [r for r in indexed if r["severity"] in ("P1", "P2")]
            logger.info("[%s] 报告入库: %d 份 (P1/P2: %d)", session_id, len(indexed), len(p1p2))
    except Exception as e:
        logger.warning("[%s] 报告索引失败: %s", session_id, e)

    # Record cross-target experience (before db close)
    try:
        profile_body = _build_target_profile(target_url, session_id, messages)
        tech_sig = generalize_tech_stack(profile_body) or "未知技术栈"
        acc_lower = accumulated_text.lower()
        if any(kw in acc_lower for kw in ["safeline", "waf", "403 forbidden", "雷池"]):
            await record_technique(db, tech_sig, "WAF detected", "blocked", "WAF")
        if "rate limit" not in acc_lower and "captcha" not in acc_lower and "验证码" not in acc_lower:
            if any(kw in acc_lower for kw in ["11105", "密码错误"]):
                await record_technique(db, tech_sig, "Login: no rate limiting", "info", "")
        if "x-token" in acc_lower or "bearer" in acc_lower:
            await record_technique(db, tech_sig, "Auth: token-based", "info", "")
        if "11104" in acc_lower and "11105" in acc_lower:
            await record_technique(db, tech_sig, "Login: user enumerable", "info", "")
        await record_technique(db, tech_sig, f"Stack: {tech_sig}", "info", "")
        for f in sorted(report_dir.glob(f"{session_id}__*.md")):
            if f.stat().st_size >= 200:
                text = f.read_text(encoding="utf-8")
                title = _extract_title(text) or f.stem
                await record_technique(db, tech_sig, f"Found: {title}", "success", text[:300])
        # Record source discovery as learning even without reports
        if not list(report_dir.glob(f"{session_id}__*.md")):
            if "sourcemap" in acc_lower or "SourceMap" in accumulated_text:
                await record_technique(db, tech_sig, "SourceMap: found and analyzed", "info",
                    "SourceMap yielded source code access")
            if "hzy@" in acc_lower or "默认密码" in acc_lower:
                await record_technique(db, tech_sig, "JS: hardcoded credentials found", "success",
                    "Hardcoded credentials extracted from JS bundle")
            if "11105" in acc_lower or "11104" in acc_lower:
                await record_technique(db, tech_sig, "Login: response code differentiation", "info",
                    "Login API returns distinct codes for user exists vs not")
        failed = await get_failed_paths(db, host)
        for f in failed[:3]:
            await record_technique(db, tech_sig, f"Block: {f['reason']}", "blocked", "")
    except Exception as e:
        logger.warning("[%s] technique recording failed: %s", session_id, e)

    await db.close()

    # ------------------------------------------------------------------
    # Termination + save to long-term memory
    # ------------------------------------------------------------------
    if final_status is None:
        final_status = _determine_status(status_marker, report_dir, session_id)

    # Save progress snapshot
    try:
        progress_body = _build_progress_snapshot(
            target_url, scenario, turn_count, final_status,
            report_dir, session_id,
        )
        memory_store.save_progress(target_url, progress_body, session_id)

        # Save findings to memory (skip duplicates rejected by report indexer)
        indexed_files = {r["filepath"] for r in indexed} if indexed else set()
        for f in sorted(report_dir.glob(f"{session_id}__*.md")):
            if f.stat().st_size >= 200:
                text = f.read_text(encoding="utf-8")
                title = _extract_title(text) or f.stem
                filepath_str = str(f.resolve())
                if indexed is not None and filepath_str not in indexed_files:
                    continue
                memory_store.save_finding(target_url, title, text[:2000], session_id)

        # Save/update target profile
        profile_body = _build_target_profile(target_url, session_id, messages)
        memory_store.save_target_profile(target_url, profile_body, session_id)

        # PentAGI vector memory: store findings as embeddings
        for f in sorted(report_dir.glob(f"{session_id}__*.md")):
            if f.stat().st_size >= 200:
                text = f.read_text(encoding="utf-8")
                title = _extract_title(text) or f.stem
                tech_sig = generalize_tech_stack(profile_body)
                try:
                    await store_vector(db, f"漏洞: {title}\n技术栈: {tech_sig}\n{text[:500]}", "finding")
                    await record_attack_chain(db, session_id, "recon", "发现", title)
                except Exception:
                    pass

        logger.info("[%s] 记忆已保存到 %s", session_id, MEMORY_DIR)
    except Exception as e:
        logger.warning("[%s] 记忆保存失败: %s", session_id, e)

    try: await cleanup_context(session_id)
    except Exception: pass
    await _finalize_session(settings.database_path, session_id, final_status)
    return final_status


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _determine_status(marker: str | None, report_dir: Path, session_id: str) -> str:
    has_report = False
    for f in report_dir.glob(f"{session_id}__*.md"):
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


def _extract_title(report_text: str) -> str:
    """Extract title from report frontmatter or first heading."""
    for line in report_text.splitlines():
        line = line.strip()
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip()
    return ""


def _build_progress_snapshot(
    target_url: str, scenario: str, turns: int,
    final_status: str, report_dir: Path, session_id: str,
) -> str:
    """Build a progress snapshot body for memory storage."""
    reports = list(report_dir.glob(f"{session_id}__*.md"))
    report_titles = []
    for f in reports:
        if f.stat().st_size >= 200:
            text = f.read_text(encoding="utf-8")
            title = _extract_title(text)
            if title:
                report_titles.append(f"- {title}")

    return f"""## 测试进度快照

- **目标**: {target_url}
- **场景**: {scenario}
- **完成轮次**: {turns}
- **终态**: {final_status}
- **报告数**: {len(report_titles)}

### 本会话发现
{chr(10).join(report_titles) if report_titles else '(无)'}
"""


def _build_target_profile(
    target_url: str, session_id: str, messages: list[dict],
) -> str:
    """Build a target profile from accumulated messages."""
    # Extract key info from tool results in messages
    tech_hints = []
    auth_info = []
    for m in messages[-100:]:  # Look at last 100 messages for tech info
        content = str(m.get("content", ""))
        for keyword in ["PHP", "Apache", "nginx", "Flask", "Spring", "Django",
                         "Python", "Java", "Tomcat", "Node.js", "IIS", "ASP.NET",
                         "Vue", "React", "MySQL", "MongoDB", "Redis", "Express"]:
            if keyword.lower() in content.lower():
                tech_hints.append(keyword)
        for keyword in ["PHPSESSID", "JSESSIONID", "session", "Authorization",
                         "Bearer", "token", "Cookie"]:
            if keyword.lower() in content.lower():
                auth_info.append(keyword)

    tech_str = ", ".join(list(set(tech_hints))[:10]) or "未探测"
    auth_str = ", ".join(list(set(auth_info))[:6]) or "未确认"

    return f"""## 目标画像

- **URL**: {target_url}
- **技术栈**: {tech_str}
- **认证机制**: {auth_str}
- **最近会话**: {session_id}

> 此画像随每次测试自动更新。
"""
