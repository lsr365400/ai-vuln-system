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
            "name": "browser_navigate",
            "description": "用真实浏览器打开页面（自动维护 Cookie/Session）。返回渲染后 HTML、链接、表单。登录页面用这个不是 curl_http。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "页面 URL"},
                    "timeout": {"type": "integer", "default": 30000},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_login",
            "description": "浏览器填写登录表单并提交，自动验证登录状态。Cookie 自动维护。返回确认的登录结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "登录页面 URL"},
                    "username": {"type": "string", "description": "用户名"},
                    "password": {"type": "string", "description": "密码"},
                    "username_field": {"type": "string", "default": "input[name=username]", "description": "用户名字段选择器"},
                    "password_field": {"type": "string", "default": "input[name=password]", "description": "密码字段选择器"},
                    "submit_button": {"type": "string", "default": "input[type=submit], button[type=submit]", "description": "提交按钮选择器"},
                    "timeout": {"type": "integer", "default": 30000},
                },
                "required": ["url", "username", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_extract",
            "description": "从页面提取内容：CSS 选择器、全文 HTML、包含特定文本的元素。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "可选，页面 URL"},
                    "selector": {"type": "string", "description": "CSS 选择器"},
                    "get_html": {"type": "boolean", "default": False, "description": "返回完整 HTML"},
                    "contains": {"type": "string", "description": "搜索包含此文本的元素"},
                },
                "required": [],
            },
        },
    },
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
            "name": "discover_endpoints",
            "description": "爬取一个页面，提取所有链接、表单、JS脚本和疑似API路径。应在每个新发现的页面上调用，以建立目标攻击面地图。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要爬取分析的页面 URL"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_auth",
            "description": "验证当前 session/cookie 是否已登录。登录后必须调用此工具确认，不要凭 HTML 内容手工判断。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_base": {"type": "string", "description": "目标基础 URL，如 http://example.com"},
                    "cookies": {"type": "string", "description": "Cookie 字符串，如 PHPSESSID=xxx; security=low"},
                    "test_path": {"type": "string", "default": "/index.php", "description": "用于验证登录状态的受保护路径"},
                },
                "required": ["target_base", "cookies"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec_shell",
            "description": "在沙箱临时目录执行命令（支持管道、脚本、批量枚举。危险性由系统拦截层保障，放心使用）",
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
        reasoning_buffer = ""  # Accumulate reasoning_content from chunks
        finish_reason = None

        async for chunk in stream:
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            # Yield reasoning_content as text (DeepSeek thinking mode — this is the visible output)
            if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                reasoning_buffer += delta.reasoning_content
                yield {"type": "text", "content": delta.reasoning_content}

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
                # Attach reasoning_content to tool_call for DeepSeek thinking mode
                yield {
                    "type": "tool_call",
                    "tool_call": tc,
                    "reasoning_content": reasoning_buffer,
                }

        if finish_reason == "stop":
            yield {"type": "finish", "reason": "stop", "reasoning_content": reasoning_buffer}

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
