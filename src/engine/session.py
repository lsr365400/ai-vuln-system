import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from src.config import Settings
from src.models import Session, SessionStatus
from src.database import init_db, insert_session, update_session_status, insert_event_log, track_endpoint, get_tested_endpoints, get_tested_urls, track_failed_path, get_failed_paths, record_technique, generalize_tech_stack
from src.engine.prompt_builder import build_system_prompt, compile_target_pattern, is_cross_domain
from src.engine.deepseek_client import DeepSeekClient
from src.engine.tool_executor import execute_tool_call
from src.engine.memory.hermes_store import HermesStore
from src.engine.memory.compressor import should_compress, compress_messages, estimate_tokens
from src.engine.memory.pentagi_memory import store_vector, record_attack_chain
from src.engine.report_indexer import index_all_reports
from src.safety.disk_guard import DiskGuard
from src.engine.browser_tool import cleanup_context

logger = logging.getLogger(__name__)

STATUS_MARKER_PREFIX = "STATUS:"
COMPRESS_EVERY_N_TURNS = 10  # Check compression need every N turns
MEMORY_DIR = Path("data/memory")
MAX_TOOL_CONTENT_CHARS = 50_000  # Truncate tool results to keep context manageable


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
    # Memory card — minimal summary injected into system prompt (≤300 chars)
    # ------------------------------------------------------------------
    memory_card = await _build_memory_card(db, target_url, session_id)
    system_prompt = build_system_prompt(
        core_skill_path=settings.skill_file,
        target_url=target_url,
        scenario=scenario,
        scenarios_dir=Path("scenarios"),
        temp_dir=temp_dir,
        report_dir=report_dir,
    )
    if memory_card:
        system_prompt += memory_card
        logger.info("[%s] 记忆卡注入 (%d chars)", session_id, len(memory_card))

    if user_input:
        system_prompt += f"\n\n## 用户指令\n\n用户说：{user_input}\n\n根据用户指引继续测试。"

    # Cross-domain scope
    scope_re = compile_target_pattern(target_url) if is_cross_domain(target_url) else ""
    if scope_re:
        logger.info("[%s] 跨域模式: pattern=%s", session_id, target_url)

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
            if turn_count > 1:
                need, level = should_compress(messages)
                if need:
                    logger.info(
                        "[%s] 触发压缩 (level=%s, est_tokens=%d)",
                        session_id, level, estimate_tokens(messages),
                    )
                    await compress_messages(client, messages, keep_last=12)
                    logger.info("[%s] 压缩完成 (%d messages remain)", session_id, len(messages))
                    # Re-inject fresh memory card + core skill after compression
                    fresh_memory = await _build_memory_card(db, target_url, session_id)
                    if fresh_memory:
                        messages.insert(1, {"role": "user", "content": fresh_memory})
                        logger.info("[%s] 压缩后重新注入记忆卡 (%d chars)", session_id, len(fresh_memory))
                    core_skill = settings.skill_file.read_text(encoding="utf-8")
                    messages.insert(2, {"role": "user", "content": core_skill})
                    logger.info("[%s] 压缩后重新注入速查卡 (%d chars)", session_id, len(core_skill))

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
                            result = await execute_tool_call(tc, temp_dir, report_dir, session_id=session_id, target_url=target_url, scope_re=scope_re)
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
                        # Truncate large tool results to prevent context explosion
                        if isinstance(result.get("content"), str) and len(result["content"]) > MAX_TOOL_CONTENT_CHARS:
                            result["content"] = result["content"][:MAX_TOOL_CONTENT_CHARS] + f"\n\n[truncated {len(result['content']) - MAX_TOOL_CONTENT_CHARS} chars to prevent context overflow]"
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

    tool_names: set[str] = set()
    # Record cross-target experience (before db close)
    from urllib.parse import urlparse
    host = urlparse(target_url).netloc or target_url
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
        # Record source discovery as learning — check ACTUAL tool calls, not text mentions
        cursor = await db.execute(
            "SELECT payload FROM event_log WHERE session_id=? AND event_type='tool_call'",
            (session_id,)
        )
        rows = await cursor.fetchall()
        for row in rows:
            tool_names.add(row[0])
        if "analyze_sourcemap" in tool_names:
            await record_technique(db, tech_sig, "SourceMap: found and analyzed", "info",
                "SourceMap tool was actually called")
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

    # ------------------------------------------------------------------
    # PentAGI vector memory (must run BEFORE db.close())
    # ------------------------------------------------------------------
    try:
        profile_body = _build_target_profile(target_url, session_id, messages)
        for f in sorted(report_dir.glob(f"{session_id}__*.md")):
            if f.stat().st_size >= 200:
                text = f.read_text(encoding="utf-8")
                title = _extract_title(text) or f.stem
                tech_sig = generalize_tech_stack(profile_body) or "未知技术栈"
                await store_vector(db, f"漏洞: {title}\n技术栈: {tech_sig}\n{text[:500]}", "finding")
                await record_attack_chain(db, session_id, "recon", "发现", title)
    except Exception as e:
        logger.debug("[%s] vector memory: %s", session_id, e)

    await db.close()

    # ------------------------------------------------------------------
    # Termination + save to long-term memory
    # ------------------------------------------------------------------
    if final_status is None:
        final_status = _determine_status(status_marker, report_dir, session_id)

    # Save progress snapshot
    try:
        memory_store = HermesStore(MEMORY_DIR)
        progress_body = _build_progress_snapshot(
            target_url, scenario, turn_count, final_status,
            report_dir, session_id,
            accumulated_text, tool_names,
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


async def _build_memory_card(db, target_url: str, session_id: str = "") -> str:
    """Build a ≤300 char memory summary card for system prompt injection."""
    from urllib.parse import urlparse
    from pathlib import Path

    host = urlparse(target_url).netloc or target_url

    # 1. Tech stack — check findings first for framework names, then fall back to technique_effectiveness
    FRAMEWORK_KEYWORDS = [
        "ThinkPHP", "Laravel", "Spring", "Django", "Flask", "RuoYi", "若依",
        "Tomcat", "Nginx", "Apache", "IIS", "Node.js", "Express", "FastAPI",
        "Vue", "React", "jQuery", "PHP", "Java", "Python", "Go", "MySQL"
    ]
    cursor = await db.execute(
        "SELECT title FROM reports WHERE target LIKE ? ORDER BY created_at DESC LIMIT 10",
        (f"%{host}%",),
    )
    report_titles = " ".join(r[0] for r in await cursor.fetchall())
    matched = [kw for kw in FRAMEWORK_KEYWORDS if kw.lower() in report_titles.lower()]
    if matched:
        tech_stack = "/".join(dict.fromkeys(matched[:4]))
    else:
        cursor = await db.execute(
            "SELECT tech_stack FROM technique_effectiveness WHERE technique LIKE 'Stack:%' "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        tech_stack = row[0] if row else "未探测"

    # 2. Past findings from reports table + extract key credentials from finding files
    cursor = await db.execute(
        "SELECT DISTINCT title FROM reports WHERE target LIKE ? ORDER BY created_at DESC LIMIT 6",
        (f"%{host}%",),
    )
    finding_rows = await cursor.fetchall()
    findings = [r[0][:30] for r in finding_rows]

    # Extract actionable credentials from findings (passwords, tokens)
    credentials = ""
    try:
        from src.engine.memory.hermes_store import HermesStore
        store = HermesStore(Path("data/memory"))
        # Priority-ordered keywords: most actionable first
        cred_keywords = ["hzy@", "akid", "secret", "密码", "password", "pwd", "token"]
        host_flat = host.replace(":", "-").replace(".", "-")
        for entry_line in store.get_index_entries():
            if "past_finding" not in entry_line or host_flat not in entry_line.replace(" ", "-"):
                continue
            try:
                slug = entry_line.split("](")[1].split(")")[0].replace(".md", "")
            except Exception:
                continue
            full = store.load(slug)
            if not full:
                continue
            body_lower = full.body.lower()
            for kw in cred_keywords:
                idx = body_lower.find(kw.lower())
                if idx >= 0:
                    snippet = full.body[idx:idx+80].replace("\n", " ").strip()
                    credentials += f"{snippet}; "
                    break
        if credentials:
            credentials = " | 凭据: " + credentials[:150]
    except Exception:
        pass

    # 3. WAF detection
    cursor = await db.execute(
        "SELECT evidence FROM technique_effectiveness WHERE technique='WAF detected' AND tech_stack LIKE ? LIMIT 1",
        (f"%{host}%",),
    )
    waf_row = await cursor.fetchone()

    # 4. Last session status (exclude current session)
    cursor = await db.execute(
        "SELECT status, error_msg FROM sessions WHERE target_url LIKE ? AND id != ? "
        "ORDER BY created_at DESC LIMIT 1",
        (f"%{host}%", session_id),
    )
    last_row = await cursor.fetchone()
    last_status = last_row[0] if last_row else "首测"

    # 5. Skipped steps from progress
    skipped_text = ""
    try:
        from src.engine.memory.hermes_store import HermesStore
        store = HermesStore(Path("data/memory"))
        slug = None
        for entry_line in store.get_index_entries():
            if host.replace(":", "-").replace(".", "-")[:20] in entry_line.replace(" ", "-"):
                try:
                    slug = entry_line.split("](")[1].split(")")[0].replace(".md", "")
                    break
                except Exception:
                    pass
        if slug:
            progress = store.load(slug)
            if progress and "未完成的步骤" in progress.body:
                lines = progress.body.split("未完成的步骤")[1].strip().split("\n")
                skipped = [l.strip("- ").strip() for l in lines if l.strip().startswith("-")]
                if skipped:
                    skipped_text = " | 待补: " + ", ".join(s[:25] for s in skipped[:3])
    except Exception:
        pass

    lines = [
        "## 目标记忆卡",
        f"- URL: {target_url}",
        f"- 技术栈: {tech_stack}",
    ]
    if findings:
        lines.append(f"- 已发现 ({len(findings)}): {', '.join(findings[:4])}")
    if credentials:
        lines.append(f"{credentials.strip(' |')}")
    if skipped_text:
        lines.append(f"- {skipped_text.strip(' |')}")
    lines.append(f"- 上次终态: {last_status}")
    if waf_row:
        lines.append(f"- WAF: {waf_row[0][:50]}")

    card = "\n".join(lines)
    forbidden = "## 禁止报告（不要调用 write_report）\nCORS · 用户名枚举 · 邮箱枚举 · P3及以下 · 调试模式泄露 · 安全头缺失 · 版本号暴露 · 目录索引\n"
    tool_hint = "> 详细记忆通过 search_memory / check_failed_paths / search_experience 工具按需查询\n"
    return f"\n\n{card}\n\n{forbidden}\n{tool_hint}"


def _build_progress_snapshot(
    target_url: str, scenario: str, turns: int,
    final_status: str, report_dir: Path, session_id: str,
    accumulated_text: str = "", tool_names: set[str] | None = None,
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

    # Detect planned-but-skipped steps
    skipped = []
    acc_lower = accumulated_text.lower()
    if tool_names is None:
        tool_names = set()
    if ("sourcemap" in acc_lower or "SourceMap" in accumulated_text) and "analyze_sourcemap" not in tool_names:
        skipped.append("SourceMap 分析 — 计划了但未执行")
    if "analyze_js" in acc_lower and "analyze_js" not in tool_names:
        skipped.append("JS 分析 — 提到但未调用工具")

    base = f"""## 测试进度快照

- **目标**: {target_url}
- **场景**: {scenario}
- **完成轮次**: {turns}
- **终态**: {final_status}
- **报告数**: {len(report_titles)}"""
    if report_titles:
        base += "\n\n### 本会话发现\n" + "\n".join(report_titles)
    if skipped:
        base += "\n\n### 未完成的步骤\n" + "\n".join("- " + s for s in skipped)
    return base


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
