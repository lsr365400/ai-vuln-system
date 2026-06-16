import aiosqlite
from pathlib import Path
from typing import Optional


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    scenario        TEXT NOT NULL DEFAULT 'custom',
    target_url      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
    priority        INTEGER NOT NULL DEFAULT 5,
    temp_dir        TEXT,
    report_dir      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    finished_at     TEXT,
    error_msg       TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    severity        TEXT NOT NULL,
    title           TEXT NOT NULL,
    target          TEXT NOT NULL,
    type            TEXT NOT NULL,
    fingerprint     TEXT UNIQUE,
    file_path       TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    payload         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tested_endpoints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    target_url      TEXT NOT NULL,
    method          TEXT NOT NULL DEFAULT 'GET',
    status_code     INTEGER,
    content_type    TEXT,
    body_length     INTEGER,
    url_count       INTEGER DEFAULT 0,
    snippet         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS failed_paths (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    target_url      TEXT NOT NULL,
    technique       TEXT NOT NULL,
    payload_short   TEXT,
    reason          TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def init_db(db_path: Path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(db_path))
    await db.executescript(SCHEMA)
    await db.commit()
    return db


async def insert_session(db: aiosqlite.Connection, s) -> None:
    await db.execute(
        """INSERT INTO sessions (id, project_id, scenario, target_url, status, priority, temp_dir, report_dir)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (s.id, s.project_id, s.scenario, s.target_url, s.status.value,
         s.priority, str(s.temp_dir) if s.temp_dir else None,
         str(s.report_dir) if s.report_dir else None),
    )
    await db.commit()


async def update_session_status(db: aiosqlite.Connection, session_id: str, status: str,
                                 error_msg: Optional[str] = None) -> None:
    if status in ("vuln_found", "low_roi", "need_input", "error", "stopped"):
        await db.execute(
            "UPDATE sessions SET status=?, finished_at=datetime('now'), error_msg=? WHERE id=?",
            (status, error_msg, session_id),
        )
    else:
        await db.execute(
            "UPDATE sessions SET status=?, error_msg=? WHERE id=?",
            (status, error_msg, session_id),
        )
    await db.commit()


async def list_sessions(db: aiosqlite.Connection, limit: int = 50, offset: int = 0,
                        status: str | None = None, project_id: str | None = None) -> list[dict]:
    query = "SELECT * FROM sessions WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


async def get_session(db: aiosqlite.Connection, session_id: str) -> dict | None:
    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


async def list_reports(db: aiosqlite.Connection, limit: int = 50, offset: int = 0,
                       severity: str | None = None) -> list[dict]:
    query = "SELECT * FROM reports WHERE 1=1"
    params = []
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


async def insert_event_log(db: aiosqlite.Connection, session_id: str, event_type: str, payload: str) -> None:
    await db.execute(
        "INSERT INTO event_log (session_id, event_type, payload) VALUES (?, ?, ?)",
        (session_id, event_type, payload),
    )
    await db.commit()


async def get_event_log(db: aiosqlite.Connection, session_id: str, limit: int = 500) -> list[dict]:
    cursor = await db.execute(
        "SELECT event_type, payload, created_at FROM event_log WHERE session_id = ? ORDER BY id ASC LIMIT ?",
        (session_id, limit),
    )
    rows = await cursor.fetchall()
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


async def delete_session(db: aiosqlite.Connection, session_id: str) -> bool:
    """Delete a session and its associated data. Returns True if deleted."""
    cursor = await db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
    if not await cursor.fetchone():
        return False
    await db.execute("DELETE FROM event_log WHERE session_id = ?", (session_id,))
    await db.execute("DELETE FROM tested_endpoints WHERE session_id = ?", (session_id,))
    await db.execute("DELETE FROM failed_paths WHERE session_id = ?", (session_id,))
    await db.execute("DELETE FROM reports WHERE session_id = ?", (session_id,))
    await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    await db.commit()
    return True


async def track_endpoint(db: aiosqlite.Connection, session_id: str, url: str,
                         method: str = "GET", status_code: int = 0,
                         content_type: str = "", body_length: int = 0,
                         url_count: int = 0, snippet: str = "") -> None:
    """Record a tested endpoint for coverage tracking."""
    await db.execute(
        """INSERT OR REPLACE INTO tested_endpoints
           (session_id, target_url, method, status_code, content_type, body_length, url_count, snippet)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, url, method, status_code, content_type[:200], body_length, url_count, snippet[:300]),
    )
    await db.commit()


async def get_tested_endpoints(db: aiosqlite.Connection, session_id: str) -> list[dict]:
    """Get all tested endpoints for a session."""
    cursor = await db.execute(
        "SELECT target_url, method, status_code, body_length, url_count FROM tested_endpoints WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    rows = await cursor.fetchall()
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


async def get_tested_urls(db: aiosqlite.Connection, target_host: str) -> list[str]:
    """Get all unique tested URLs across sessions for a target host."""
    cursor = await db.execute(
        "SELECT DISTINCT target_url FROM tested_endpoints WHERE target_url LIKE ?",
        (f"{target_host}%",),
    )
    rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def track_failed_path(db: aiosqlite.Connection, session_id: str, target_url: str,
                            technique: str, reason: str, payload_short: str = "") -> None:
    await db.execute(
        "INSERT INTO failed_paths (session_id, target_url, technique, payload_short, reason) VALUES (?, ?, ?, ?, ?)",
        (session_id, target_url, technique, payload_short[:200], reason),
    )
    await db.commit()


async def get_failed_paths(db: aiosqlite.Connection, target_host: str) -> list[dict]:
    """Get all failed paths for a target host across sessions."""
    cursor = await db.execute(
        "SELECT DISTINCT technique, payload_short, reason, MAX(created_at) as last_seen FROM failed_paths "
        "WHERE target_url LIKE ? GROUP BY technique, reason ORDER BY last_seen DESC LIMIT 30",
        (f"{target_host}%",),
    )
    rows = await cursor.fetchall()
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in rows]
