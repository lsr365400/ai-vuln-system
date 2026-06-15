"""Context compressor — prevent message overflow in long sessions.

Strategy:
    1. Estimate token count of the message list.
    2. When estimate exceeds a threshold, take the oldest N messages and
       ask DeepSeek to summarise them into a compact "early findings" blob.
    3. Replace the old messages with a single synthetic system message.
    4. Repeat as needed.

The summary is placed at the TOP of the message list (after system prompt)
so the AI always retains awareness of early work.
"""

import logging
from typing import Optional

from src.config import Settings
from src.engine.deepseek_client import DeepSeekClient

logger = logging.getLogger(__name__)

# Thresholds
TOKEN_WARN_RATIO = 0.6   # When estimated tokens reach 60% of context → compress
TOKEN_HARD_RATIO = 0.85  # 85% → force compress
ESTIMATED_CHARS_PER_TOKEN = 2.5  # Rough heuristic for Chinese + code
DEFAULT_CONTEXT_TOKENS = 1_000_000  # deepseek-v4-pro 1M

COMPRESS_PROMPT = """你是一个会话摘要工具。请用中文将以下测试消息历史压缩为一份结构化摘要。只提取安全测试相关的关键信息：

1. **已完成测试**: 列出已经测试过的端点、参数、攻击类型
2. **关键发现**: 列出所有发现（漏洞/可疑点/信息泄露），标注是否已验证
3. **目标信息**: 技术栈、框架版本、WAF、认证机制等已确认的信息
4. **当前状态**: 正在测试什么，下一步计划测什么

输出格式（Markdown 片段，不要其他解释）：

## 早期测试摘要

### 已测试范围
- ...

### 关键发现
- ...

### 目标画像
- ...

### 当前进度
- ...

下面是需要压缩的消息历史：
"""


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimation from character count."""
    total_chars = 0
    for m in messages:
        if isinstance(m.get("content"), str):
            total_chars += len(m["content"])
        elif m.get("content") is None and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                total_chars += len(str(tc))
        elif m.get("role") == "tool":
            total_chars += len(str(m.get("content", "")))
    return int(total_chars / ESTIMATED_CHARS_PER_TOKEN)


def should_compress(messages: list[dict], context_size: int = DEFAULT_CONTEXT_TOKENS) -> tuple[bool, str]:
    """Check whether compression is needed. Returns (should_compress, level)."""
    est = estimate_tokens(messages)
    if est > context_size * TOKEN_HARD_RATIO:
        return True, "hard"
    if est > context_size * TOKEN_WARN_RATIO:
        return True, "warn"
    return False, "ok"


async def compress_messages(
    client: DeepSeekClient,
    messages: list[dict],
    keep_last: int = 15,
) -> Optional[str]:
    """Compress old messages into a summary.

    Keeps the last `keep_last` messages (most recent turn + tool results).
    Summarises everything before that.
    Returns the summary text, or None if nothing to compress.
    """
    if len(messages) <= keep_last:
        return None

    old = messages[:-keep_last]
    recent = messages[-keep_last:]

    # Build the compression request
    old_text = _messages_to_text(old)
    if len(old_text) < 500:
        return None  # Not worth compressing

    prompt = COMPRESS_PROMPT + "\n\n" + old_text[:50000]  # Truncate to safe size

    summary = ""
    try:
        async for event in client.chat_stream(
            system_prompt="你是一个精确的信息压缩器。",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        ):
            if event["type"] == "text":
                summary += event["content"]
    except Exception as e:
        logger.warning("compression failed: %s", e)
        return None

    if not summary.strip():
        return None

    # Replace the old messages with the summary
    messages.clear()
    messages.append({
        "role": "user",
        "content": summary.strip(),
    })
    messages.extend(recent)

    logger.info(
        "compressed %d messages → %d chars summary (kept last %d)",
        len(old), len(summary), len(recent),
    )
    return summary


def _messages_to_text(messages: list[dict]) -> str:
    """Convert message list to a compact text representation for summarisation."""
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content")

        if content and isinstance(content, str):
            lines.append(f"[{role}]: {content[:500]}")
        elif m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                args = fn.get("arguments", "")[:200]
                lines.append(f"[{role} → tool:{name}]: {args}")
        elif role == "tool":
            lines.append(f"[tool_result]: {str(content)[:300]}")
    return "\n".join(lines)
