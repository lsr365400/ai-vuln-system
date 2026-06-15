# src/engine/deepseek_client.py
import json
import logging
from typing import AsyncIterator

from openai import AsyncOpenAI

from src.config import Settings

logger = logging.getLogger(__name__)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "curl_http",
            "description": "发送 HTTP 请求（支持 GET/POST/PUT/DELETE），返回响应头和响应体",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整 URL"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                        "default": "GET",
                    },
                    "headers": {
                        "type": "object",
                        "description": 'HTTP 请求头，如 {"Cookie": "session=xxx"}',
                    },
                    "body": {
                        "type": "string",
                        "description": "请求体（POST/PUT 时使用）",
                    },
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec_shell",
            "description": "在沙箱临时目录内执行 shell 命令（仅允许 curl/wget/python/nslookup/dig 等网络测试工具）",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_report",
            "description": "发现漏洞时，将漏洞报告写入文件。调用前必须通过七问验证门",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "报告文件名，如 session-id__sqli-login.md"},
                    "content": {"type": "string", "description": "Markdown 格式的完整漏洞报告"},
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_session",
            "description": "结束当前测试会话",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["VULN_FOUND", "LOW_ROI", "NEED_INPUT"],
                        "description": "会话终止状态",
                    },
                    "summary": {
                        "type": "string",
                        "description": "测试总结（发现的漏洞/测试过但未发现的方向）",
                    },
                },
                "required": ["status", "summary"],
            },
        },
    },
]


class DeepSeekClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        self.model = settings.deepseek_model

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 4096,
    ) -> AsyncIterator[dict]:
        full_messages = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=max_tokens,
            stream=True,
        )

        tool_call_buffer: dict[int, dict] = {}
        finish_reason = None

        async for chunk in stream:
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            if delta.content:
                yield {"type": "text", "content": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_buffer:
                        tool_call_buffer[idx] = {
                            "id": tc.id or "",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_call_buffer[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_buffer[idx]["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_call_buffer[idx]["function"]["arguments"] += tc.function.arguments

        if finish_reason == "tool_calls" and tool_call_buffer:
            for tc in tool_call_buffer.values():
                try:
                    tc["function"]["arguments_parsed"] = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    tc["function"]["arguments_parsed"] = {}
                yield {"type": "tool_call", "tool_call": tc}

        if finish_reason == "stop":
            yield {"type": "finish", "reason": "stop"}

    async def health_check(self) -> bool:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                stream=False,
            )
            return response.choices[0].message.content is not None
        except Exception:
            return False
