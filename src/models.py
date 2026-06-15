from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class SessionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    VULN_FOUND = "vuln_found"
    LOW_ROI = "low_roi"
    NEED_INPUT = "need_input"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass
class Session:
    id: str
    project_id: str
    scenario: str
    target_url: str
    status: SessionStatus = SessionStatus.QUEUED
    priority: int = 5
    temp_dir: Optional[Path] = None
    report_dir: Optional[Path] = None
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_msg: Optional[str] = None


@dataclass
class ReportMeta:
    session_id: str
    severity: str  # P1 / P2 / P3
    title: str
    target: str
    type: str
    file_path: Path
    fingerprint: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
