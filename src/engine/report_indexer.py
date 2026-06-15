"""Report indexer — parse report .md files, dedup, store to SQLite, email notify."""

import hashlib
import logging
import smtplib
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

# SMTP config (QQ mail)
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465
SMTP_USER = "2386088090@qq.com"
SMTP_PASS = "zqjhwhnokxuwecgg"
NOTIFY_TO = "3766177951@qq.com"


def parse_frontmatter(text: str) -> dict:
    """Parse YAML-like frontmatter from markdown report."""
    m = FRONTMATTER_RE.search(text)
    if not m:
        return {}
    raw = m.group(1)
    meta = {}
    for line in raw.strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    return meta


def make_fingerprint(title: str, target: str) -> str:
    return hashlib.sha256(f"{title}|{target}".encode()).hexdigest()


async def index_report(db: aiosqlite.Connection, session_id: str, filepath: Path) -> Optional[dict]:
    """Index a single report file. Returns the report dict if new, None if duplicate."""
    if not filepath.exists() or filepath.stat().st_size < 200:
        return None

    text = filepath.read_text(encoding="utf-8")
    meta = parse_frontmatter(text)
    severity = meta.get("severity", "P4")
    title = meta.get("title", filepath.stem)
    target = meta.get("target", "unknown")
    vuln_type = meta.get("type", "unknown")
    fingerprint = make_fingerprint(title, target)

    # Check duplicate
    cursor = await db.execute("SELECT id FROM reports WHERE fingerprint = ?", (fingerprint,))
    existing = await cursor.fetchone()
    if existing:
        logger.info("报告已存在(去重): %s", title)
        return None

    # Insert
    await db.execute(
        """INSERT INTO reports (session_id, severity, title, target, type, fingerprint, file_path)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session_id, severity, title, target, vuln_type, fingerprint, str(filepath)),
    )
    await db.commit()

    logger.info("报告入库: [%s] %s", severity, title)
    return {
        "session_id": session_id,
        "severity": severity,
        "title": title,
        "target": target,
        "type": vuln_type,
        "filepath": str(filepath),
    }


def send_email(severity: str, title: str, target: str, body: str) -> bool:
    """Send email notification via QQ SMTP (SSL)."""
    subject = f"[{severity}] {title}"

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_TO
    msg["Subject"] = subject

    html_body = f"""
    <h2>{severity} - {title}</h2>
    <p><strong>目标:</strong> {target}</p>
    <hr>
    <pre>{body[:5000]}</pre>
    <hr>
    <p><small>AI 漏洞挖掘系统 自动通知</small></p>
    """
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [NOTIFY_TO], msg.as_string())
        logger.info("邮件已发送: %s → %s", subject, NOTIFY_TO)
        return True
    except Exception as e:
        logger.error("邮件发送失败: %s", e)
        return False


async def index_all_reports(
    db: aiosqlite.Connection,
    session_id: str,
    report_dir: Path,
) -> list[dict]:
    """Scan report dir for session reports, index new ones, notify for P1/P2."""
    indexed = []
    for f in sorted(report_dir.glob(f"{session_id}__*.md")):
        report = await index_report(db, session_id, f)
        if report:
            indexed.append(report)

    # Send email for P1/P2
    for r in indexed:
        if r["severity"] in ("P1", "P2"):
            filepath = Path(r["filepath"])
            if filepath.exists():
                body = filepath.read_text(encoding="utf-8")
                send_email(r["severity"], r["title"], r["target"], body)

    return indexed
