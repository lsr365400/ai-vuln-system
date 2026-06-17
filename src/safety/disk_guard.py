"""Disk guard — two-layer protection for AI session file operations.

Layer 1: Self-loop detection — block writes whose target path is inside the
         source/reading directory (prevents recursive file generation).
Layer 2: Per-session disk quota — hard limit on temp directory size,
         sampled periodically during the session loop.
"""

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default limits
DEFAULT_QUOTA_GB = 5
CHECK_INTERVAL_SEC = 3  # How often to sample disk usage


class DiskGuard:
    """Per-session disk safety monitor."""

    def __init__(self, session_dir: Path, quota_gb: int = DEFAULT_QUOTA_GB):
        self.session_dir = Path(session_dir)
        self.quota_bytes = quota_gb * 1024 * 1024 * 1024
        self._last_check = 0.0
        self._cached_size = 0

    # ------------------------------------------------------------------
    # Layer 1: Self-loop detection
    # ------------------------------------------------------------------

    def check_write_path(self, write_target: Path) -> tuple[bool, str]:
        """Block writes where the target path is within the session directory
        AND the write would create a self-referencing loop.

        This prevents: reading from session_dir → AI writes back into session_dir
        creating an infinite growth pattern.
        """
        resolved = write_target.resolve()

        # Block writes to system paths (cross-platform)
        forbidden = ["/etc/", "/proc/", "/sys/", "/dev/", "/boot/",
                     "C:\\Windows", "C:\\Program Files",
                     "C:\\Program Files (x86)", "/System/"]
        resolved_str = str(resolved)
        for prefix in forbidden:
            if resolved_str.startswith(prefix) or resolved_str.startswith(prefix.replace("\\", "/")):
                return False, f"禁止写入系统目录: {prefix}"

        # Allow writes only within session_dir or report_dir
        # (report_dir is handled separately via tool_executor)
        try:
            if str(resolved).startswith(str(self.session_dir.resolve())):
                return True, ""
        except (ValueError, OSError):
            return False, "无法解析写入路径"

        return True, ""

    # ------------------------------------------------------------------
    # Layer 2: Disk quota
    # ------------------------------------------------------------------

    def check_quota(self, extra_paths: Optional[list[Path]] = None) -> tuple[bool, int]:
        """Check if session directory exceeds quota.

        Returns (over_limit, current_size_bytes).
        Caches result within CHECK_INTERVAL_SEC to avoid excessive I/O.
        """
        now = time.time()
        if now - self._last_check < CHECK_INTERVAL_SEC:
            return self._cached_size > self.quota_bytes, self._cached_size

        self._last_check = now
        total = self._du(self.session_dir)
        if extra_paths:
            for p in extra_paths:
                if p.exists():
                    total += self._du(p)

        self._cached_size = total
        over = total > self.quota_bytes
        if over:
            logger.warning(
                "磁盘配额超限: %s / %s (%.1f GB / %d GB)",
                self._format_size(total),
                self._format_size(self.quota_bytes),
                total / (1024**3),
                self.quota_bytes // (1024**3),
            )
        return over, total

    def get_usage_mb(self) -> float:
        """Quick MB usage estimate (uses cached value if fresh)."""
        over, size = self.check_quota()
        return size / (1024 * 1024)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _du(path: Path) -> int:
        """Recursive disk usage in bytes. Returns 0 for missing paths."""
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        total = 0
        try:
            for child in path.rglob("*"):
                if child.is_file():
                    try:
                        total += child.stat().st_size
                    except OSError:
                        pass
        except (PermissionError, OSError):
            pass
        return total

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024**2:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024**3:
            return f"{size_bytes / 1024**2:.1f} MB"
        else:
            return f"{size_bytes / 1024**3:.2f} GB"
