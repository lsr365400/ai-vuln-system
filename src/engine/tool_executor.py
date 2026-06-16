import asyncio
import logging
import re
import shlex
from pathlib import Path
from typing import Any

import httpx

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
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
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


async def execute_write_report(tool_args: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    filename = tool_args["filename"]
    content = tool_args["content"]

    if ".." in filename or "/" in filename or "\\" in filename:
        return {"error": "文件名不能包含路径分隔符"}

    filepath = report_dir / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
    return {"file_path": str(filepath), "size": len(content)}


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
    elif name == "finish_session":
        result = {"acknowledged": True, "status": args.get("status", "UNKNOWN")}
    else:
        result = {"error": f"Unknown tool: {name}"}

    return {
        "tool_call_id": tool_call["id"],
        "role": "tool",
        "content": str(result),
    }
