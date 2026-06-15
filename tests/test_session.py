from pathlib import Path
from src.engine.session import detect_status_marker, _determine_status
from src.safety.disk_guard import DiskGuard


def test_detect_vuln_found():
    text = "测试完成\nSTATUS: VULN_FOUND"
    assert detect_status_marker(text) == "VULN_FOUND"


def test_detect_low_roi():
    text = "无发现\nSTATUS: LOW_ROI"
    assert detect_status_marker(text) == "LOW_ROI"


def test_detect_need_input():
    text = "需要信息\nSTATUS: NEED_INPUT"
    assert detect_status_marker(text) == "NEED_INPUT"


def test_detect_no_marker():
    text = "测试完成，没有标记"
    assert detect_status_marker(text) is None


def test_detect_marker_not_last_line():
    text = "STATUS: VULN_FOUND\n其他文字"
    assert detect_status_marker(text) == "VULN_FOUND"


def test_determine_status_vuln_found_with_report(tmp_path):
    (tmp_path / "abc123__sqli.md").write_text("x" * 200)
    result = _determine_status("VULN_FOUND", tmp_path, "abc123")
    assert result == "vuln_found"


def test_determine_status_vuln_found_no_report(tmp_path):
    result = _determine_status("VULN_FOUND", tmp_path, "abc123")
    assert result == "low_roi"


def test_determine_status_low_roi_has_report(tmp_path):
    (tmp_path / "abc123__idor.md").write_text("x" * 200)
    result = _determine_status("LOW_ROI", tmp_path, "abc123")
    assert result == "vuln_found"


def test_determine_status_no_marker_has_report(tmp_path):
    (tmp_path / "abc123__rce.md").write_text("x" * 200)
    result = _determine_status(None, tmp_path, "abc123")
    assert result == "vuln_found"


def test_disk_guard_under_limit(tmp_path):
    (tmp_path / "small.txt").write_text("small")
    guard = DiskGuard(tmp_path, quota_gb=5)
    over, size = guard.check_quota()
    assert not over
    assert size > 0


def test_disk_guard_write_path_blocks_system():
    guard = DiskGuard(Path("/tmp/test"), quota_gb=1)
    ok, reason = guard.check_write_path(Path("C:/Windows/System32/test.dll"))
    assert not ok
    assert "禁止写入系统目录" in reason


def test_disk_guard_write_path_allows_session_dir(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    guard = DiskGuard(session_dir, quota_gb=1)
    ok, _ = guard.check_write_path(session_dir / "output.txt")
    assert ok


def test_determine_status_no_marker_no_report(tmp_path):
    result = _determine_status(None, tmp_path, "abc123")
    assert result == "error"
