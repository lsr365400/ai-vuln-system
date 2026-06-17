import asyncio
import logging
import re
import shlex
from pathlib import Path
from typing import Any

import httpx
import aiosqlite

logger = logging.getLogger(__name__)

# No whitelist — safety enforced by BLOCKED_PATTERNS only

BLOCKED_PATTERNS = [
    "rm -rf", "rm -r", "rmdir",
    ">/dev/", ">/etc/", ">/proc/", ">/sys/",
    "mkfs", "dd if=", "shutdown", "reboot",
    "nc -l", "nc -e", "bash -i", "/bin/bash",
    "> /dev/sda", "chmod 777", "chown",
    "wget -O /", "curl -o /",
]


def _is_command_safe(command: str, allowed_dir: Path) -> tuple[bool, str]:
    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            return False, f"命令包含危险模式: {pattern}"

    cmd_parts = shlex.split(command)
    if not cmd_parts:
        return False, "空命令"

    if " -o " in command or " -O " in command or " --output-document" in command:
        return False, "不允许通过 exec_shell 写入文件，使用 write_report"

    return True, ""


async def execute_curl(tool_args: dict[str, Any], timeout: int = 30,
                       session_id: str = "", temp_dir: Path = None) -> dict[str, Any]:
    url = tool_args["url"]
    method = tool_args.get("method", "GET").upper()
    headers = tool_args.get("headers", {})
    # WAF/IDS often block python-httpx default UA — use browser UA instead
    if "User-Agent" not in headers and "user-agent" not in headers:
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    body = tool_args.get("body")

    # Use persistent cookie jar per session (cookies survive across curl calls)
    import json as _json
    if session_id and temp_dir:
        cookie_file = temp_dir / "cookies.json"
        saved_cookies = {}
        if cookie_file.exists():
            try: saved_cookies = _json.loads(cookie_file.read_text())
            except Exception: pass
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=False, cookies=saved_cookies)
    else:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        cookie_file = None

    try:
        response = await client.request(method=method, url=url, headers=headers, content=body)

        # Save cookies for next call in this session
        if cookie_file:
            try: cookie_file.write_text(_json.dumps(dict(client.cookies)))
            except Exception: pass

        # Extract URLs from body
        urls = re.findall(r'(?:href|src|action)=["\']([^"\']+)["\']', response.text, re.I)
        urls += re.findall(r'https?://[^\s"\'<>]{3,}', response.text)
        urls = list(dict.fromkeys(urls))

        # Save full body to temp file if large (design guide Ch8.2)
        body_path = None
        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": response.text,
            "body_length": len(response.text),
            "content_type": response.headers.get("content-type", ""),
            "urls_found": urls[:30],
            "url_count": len(urls),
        }
    except httpx.TimeoutException:
        return {"error": f"请求超时 ({timeout}s)", "status_code": 0}
    except Exception as e:
        return {"error": str(e), "status_code": 0}
    finally:
        await client.aclose()


async def execute_shell(tool_args: dict[str, Any], allowed_dir: Path) -> dict[str, Any]:
    command = tool_args["command"]
    safe, reason = _is_command_safe(command, allowed_dir)
    if not safe:
        return {"error": reason, "stdout": "", "stderr": reason}

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(allowed_dir),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace")[:4000],
            "stderr": stderr.decode("utf-8", errors="replace")[:2000],
        }
    except asyncio.TimeoutError:
        return {"error": "命令执行超时 (30s)", "stdout": "", "stderr": "timeout"}


async def execute_discover(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Crawl a page and discover all linked endpoints/forms/scripts."""
    url = tool_args["url"]
    timeout = tool_args.get("timeout", 30)
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            text = response.text
            # Extract links, forms, scripts, API-like paths
            hrefs = re.findall(r'href=["\']([^"\']+)["\']', text, re.I)
            actions = re.findall(r'action=["\']([^"\']+)["\']', text, re.I)
            scripts = re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', text, re.I)
            api_paths = re.findall(r'["\']((?:/api/|/v\d/|/ajax/)[^"\']+)["\']', text, re.I)
            all_links = list(dict.fromkeys(hrefs + actions + scripts + api_paths))
            return {
                "url": url,
                "status_code": response.status_code,
                "links": all_links[:50],
                "forms": len(actions),
                "scripts": len(scripts),
                "api_hints": api_paths[:20],
                "total_found": len(all_links),
            }
    except Exception as e:
        return {"error": str(e)}


async def execute_check_auth(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Check if current cookies/session are authenticated by probing a protected page."""
    cookies = tool_args.get("cookies", "")
    target = tool_args["target_base"]
    test_path = tool_args.get("test_path", "/index.php")
    try:
        headers = {"Cookie": cookies} if cookies else {}
        headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(f"{target.rstrip('/')}{test_path}", headers=headers)
            redirected = response.status_code in (301, 302, 303, 307, 308)
            location = response.headers.get("location", "")
            body = response.text[:500].lower()
            # Auth indicators
            login_signs = ["login", "sign in", "密码", "用户名", "not logged in", "session expired"]
            welcome_signs = ["welcome", "dashboard", "logout", "log out", "欢迎", "退出"]
            redirects_to_login = redirected and any(kw in location.lower() for kw in ["login", "signin"])
            has_login = any(kw in body for kw in login_signs)
            has_welcome = any(kw in body for kw in welcome_signs)
            if redirects_to_login:
                ok, why = False, f"重定向到登录页: {location}"
            elif has_login and not has_welcome:
                ok, why = False, "页面仍含登录表单"
            elif has_welcome:
                ok, why = True, "含已登录标志"
            elif redirected and "login" not in location.lower():
                ok, why = True, f"重定向到: {location}"
            elif response.status_code == 200 and not has_login:
                ok, why = True, "正常访问受保护页面"
            else:
                ok, why = False, f"status={response.status_code}，假设未登录"
            return {"authenticated": ok, "evidence": why, "status_code": response.status_code,
                    "redirect": location if redirected else None, "test_url": f"{target}{test_path}"}
    except Exception as e:
        return {"authenticated": False, "evidence": f"请求失败: {e}"}


async def execute_analyze_js(tool_args: dict[str, Any], temp_dir: Path) -> dict[str, Any]:
    """Download a JS file and analyze it with jsluice for routes, secrets, and endpoints."""
    url = tool_args["url"]
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            js_content = response.text
            if response.status_code != 200 or not js_content.strip():
                return {"error": f"JS 文件获取失败 (status={response.status_code}) — try with Referer: <target>"}

            js_file = temp_dir / "analyze_target.js"
            js_file.write_text(js_content, encoding="utf-8")

            urls_output = ""
            secrets_output = ""
            proc = await asyncio.create_subprocess_exec(
                "jsluice", "urls", str(js_file),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            urls_output = stdout.decode("utf-8", errors="replace")[:6000]

            proc2 = await asyncio.create_subprocess_exec(
                "jsluice", "secrets", str(js_file),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=15)
            secrets_output = stdout2.decode("utf-8", errors="replace")[:3000]

            return {
                "file": url,
                "size": len(js_content),
                "urls_found": urls_output,
                "secrets_found": secrets_output or "(none)",
            }
    except Exception as e:
        return {"error": str(e)}


async def execute_brute_force(tool_args: dict[str, Any], temp_dir: Path) -> dict[str, Any]:
    """Run ffuf password brute-force against a login endpoint (non-edu targets only)."""
    url = tool_args["url"]
    username = tool_args.get("username", "admin")
    wordlist = tool_args.get("wordlist", "/usr/share/wordlists/rockyou.txt")
    password_field = tool_args.get("password_field", "password")
    username_field = tool_args.get("username_field", "username")
    success_keyword = tool_args.get("success_keyword", "login_success")
    success_code = tool_args.get("success_code", 302)
    max_words = tool_args.get("max_words", 200)

    wordlist_path = Path(wordlist)
    if not wordlist_path.exists():
        return {"error": f"字典文件不存在: {wordlist}，请先上传"}

    postdata = f"{username_field}={username}&{password_field}=FUZZ"
    cmd = (
        f"ffuf -u '{url}' -d '{postdata}' -w {wordlist} "
        f"-H 'Content-Type: application/x-www-form-urlencoded' "
        f"-mc {success_code} -mw {max_words} "
        f"-mr '{success_keyword}' -t 10 -of json -o {temp_dir/'ffuf_result.json'} "
        f"-s 2>&1 | head -20"
    )

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(temp_dir),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode("utf-8", errors="replace")
        return {
            "results": output[:3000] or "(无匹配)",
            "wordlist": wordlist,
            "error": stderr.decode("utf-8", errors="replace")[:500],
        }
    except asyncio.TimeoutError:
        return {"error": "爆破超时 (120s)", "results": ""}
    except Exception as e:
        return {"error": str(e)}


async def execute_sourcemap_parse(tool_args: dict[str, Any], temp_dir: Path) -> dict[str, Any]:
    """Download and parse a .js.map SourceMap file to extract original source code."""
    url = tool_args["url"]
    filter_keyword = tool_args.get("filter", "")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"error": f"Download failed ({resp.status_code}) — try curl_http with browser User-Agent or browser_navigate"}
            map_file = temp_dir / "source.map"
            map_file.write_bytes(resp.content)

        proc = await asyncio.create_subprocess_exec(
            "node", "tools/sourcemap-parser.js", str(map_file), filter_keyword,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace")

        source_count = output.count("=== Source Files ===")
        api_hints = [l.strip() for l in output.split("\n") if "api" in l.lower() or "router" in l.lower() or "auth" in l.lower()]

        return {
            "sources_found": source_count,
            "api_hints": api_hints[:20],
            "output": output[:6000],
            "full_size": len(resp.content),
        }
    except Exception as e:
        return {"error": str(e)}


async def execute_write_report(tool_args: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    filename = tool_args["filename"]
    content = tool_args["content"]

    if ".." in filename or "/" in filename or "\\" in filename:
        return {"error": "文件名不能包含路径分隔符"}

    filepath = report_dir / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
    return {"file_path": str(filepath), "size": len(content)}


async def execute_search_memory(args: dict, session_id: str = "") -> dict:
    """Search Hermes memory + vector memory for a query."""
    query = args.get("query", "")
    target_url = args.get("target_url", "")
    results = []

    # 1. Hermes file search by keyword
    from src.engine.memory.hermes_store import HermesStore
    store = HermesStore(Path("data/memory"))
    for entry_line in store.get_index_entries():
        if query.lower() in entry_line.lower():
            results.append(f"[file] {entry_line}")

    # 2. Vector semantic search
    from src.engine.memory.pentagi_memory import search_vectors
    db_path = Path("data/db.sqlite3")
    db = None
    try:
        db = await aiosqlite.connect(str(db_path))
        similar = await search_vectors(db, query, "finding", top_k=3)
        for s in similar:
            results.append(f"[vector {s['similarity']}] {s['content'][:300]}")
    except Exception:
        pass  # vector search is best-effort, don't block on failure
    finally:
        if db is not None:
            await db.close()

    if not results:
        return {"query": query, "results": [], "hint": "无匹配记忆，这可能是新的攻击面"}
    return {"query": query, "results": results[:5]}


async def execute_check_failed_paths(args: dict) -> dict:
    """Check failed paths for a host."""
    host = args.get("host", "")
    db_path = Path("data/db.sqlite3")
    db = None
    try:
        db = await aiosqlite.connect(str(db_path))
        cursor = await db.execute(
            "SELECT technique, reason FROM failed_paths WHERE target_url LIKE ? "
            "GROUP BY technique HAVING COUNT(*) >= 2 ORDER BY COUNT(*) DESC LIMIT 10",
            (f"%{host}%",),
        )
        rows = await cursor.fetchall()
        failed = [{"technique": r[0], "reason": r[1]} for r in rows]
        return {"host": host, "failed_paths": failed, "total": len(failed)}
    finally:
        if db is not None:
            await db.close()


async def execute_search_experience(args: dict) -> dict:
    """Search cross-target effective techniques."""
    tech_hint = args.get("tech_hint", "")
    db_path = Path("data/db.sqlite3")
    db = None
    try:
        db = await aiosqlite.connect(str(db_path))
        cursor = await db.execute(
            "SELECT tech_stack, technique, outcome, count, evidence FROM technique_effectiveness "
            "WHERE tech_stack LIKE ? AND outcome IN ('success', 'info') ORDER BY count DESC LIMIT 8",
            (f"%{tech_hint}%",),
        )
        rows = await cursor.fetchall()
        cols = [c[0] for c in cursor.description]
        techniques = []
        for r in rows:
            d = dict(zip(cols, r))
            techniques.append({
                "tech_stack": d["tech_stack"],
                "technique": d["technique"],
                "outcome": d["outcome"],
                "count": d["count"],
            })
        return {"tech_hint": tech_hint, "techniques": techniques}
    finally:
        if db is not None:
            await db.close()


async def execute_tool_call(
    tool_call: dict,
    temp_dir: Path,
    report_dir: Path,
    session_id: str = "",
) -> dict[str, Any]:
    name = tool_call["function"]["name"]
    args = tool_call["function"].get("arguments_parsed", {})

    if name in ("browser_navigate", "browser_login", "browser_extract"):
        from src.engine.browser_tool import execute_browser_tool
        return await execute_browser_tool(session_id, temp_dir, tool_call)
    elif name == "curl_http":
        result = await execute_curl(args, session_id=session_id, temp_dir=temp_dir)
    elif name == "discover_endpoints":
        result = await execute_discover(args)
    elif name == "check_auth":
        result = await execute_check_auth(args)
    elif name == "exec_shell":
        result = await execute_shell(args, temp_dir)
    elif name == "write_report":
        result = await execute_write_report(args, report_dir)
    elif name == "analyze_js":
        result = await execute_analyze_js(args, temp_dir)
    elif name == "brute_force":
        result = await execute_brute_force(args, temp_dir)
    elif name == "search_memory":
        result = await execute_search_memory(args, session_id)
    elif name == "check_failed_paths":
        result = await execute_check_failed_paths(args)
    elif name == "search_experience":
        result = await execute_search_experience(args)
    elif name == "analyze_sourcemap":
        result = await execute_sourcemap_parse(args, temp_dir)
    elif name == "finish_session":
        result = {"acknowledged": True, "status": args.get("status", "UNKNOWN")}
    else:
        result = {"error": f"Unknown tool: {name}"}

    return {
        "tool_call_id": tool_call["id"],
        "role": "tool",
        "content": str(result),
    }
