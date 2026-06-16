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
