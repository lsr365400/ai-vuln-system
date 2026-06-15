import tempfile
from pathlib import Path
from src.engine.tool_executor import (
    _is_command_safe,
    execute_write_report,
    execute_curl,
)


def test_is_command_safe_allows_curl():
    safe, reason = _is_command_safe("curl https://example.com", Path("/tmp/test"))
    assert safe


def test_is_command_safe_blocks_dangerous():
    safe, reason = _is_command_safe("rm -rf /", Path("/tmp/test"))
    assert not safe
    assert "危险" in reason


def test_is_command_safe_requires_whitelist():
    safe, reason = _is_command_safe("cat /etc/passwd", Path("/tmp/test"))
    assert not safe
    assert "白名单" in reason


async def test_write_report(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    result = await execute_write_report(
        {"filename": "test.md", "content": "# Test Report"},
        report_dir,
    )
    assert result["size"] > 0
    assert (report_dir / "test.md").exists()


async def test_write_report_blocks_path_traversal():
    result = await execute_write_report(
        {"filename": "../../etc/passwd", "content": "bad"},
        Path("/tmp/test"),
    )
    assert "error" in result
