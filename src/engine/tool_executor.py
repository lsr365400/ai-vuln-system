import asyncio
import logging
import shlex
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ALLOWED_COMMANDS = {"curl", "wget", "nslookup", "dig", "host", "whois", "python", "python3"}

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

    cmd_name = cmd_parts[0]
    if cmd_name not in ALLOWED_COMMANDS:
        return False, f"命令不在白名单: {cmd_name}"

    if " -o " in command or " -O " in command or " --output-document" in command:
        return False, "不允许通过 exec_shell 写入文件，使用 write_report"

    return True, ""


async def execute_curl(tool_args: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    url = tool_args["url"]
    method = tool_args.get("method", "GET").upper()
    headers = tool_args.get("headers", {})
    body = tool_args.get("body")

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
            )
            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response.text[:8000],
                "body_length": len(response.text),
            }
    except httpx.TimeoutException:
        return {"error": f"请求超时 ({timeout}s)", "status_code": 0}
    except Exception as e:
        return {"error": str(e), "status_code": 0}


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
) -> dict[str, Any]:
    name = tool_call["function"]["name"]
    args = tool_call["function"].get("arguments_parsed", {})

    if name == "curl_http":
        result = await execute_curl(args)
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
