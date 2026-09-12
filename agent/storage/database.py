import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from agent.config import Config

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_PROCESSED_EMAILS = """
CREATE TABLE IF NOT EXISTS processed_emails (
    email_id     TEXT PRIMARY KEY,
    processed_at TEXT DEFAULT (datetime('now')),
    label        TEXT,
    is_urgent    INTEGER,
    summary      TEXT,
    action_taken TEXT
)
"""

_CREATE_PENDING_APPROVALS = """
CREATE TABLE IF NOT EXISTS pending_approvals (
    id                  TEXT PRIMARY KEY,
    original_email_id   TEXT NOT NULL,
    original_from       TEXT,
    original_subject    TEXT,
    original_body       TEXT,
    original_message_id TEXT,
    classification      TEXT,
    proposed_slots      TEXT,
    approval_thread_id  TEXT,
    status              TEXT DEFAULT 'PENDING',
    created_at          TEXT DEFAULT (datetime('now')),
    resolved_at         TEXT
)
"""

_CREATE_AUDIT_LOG = """
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT    DEFAULT (datetime('now')),
    email_id   TEXT,
    event_type TEXT,
    details    TEXT
)
"""


# ---------------------------------------------------------------------------
# Typed model
# ---------------------------------------------------------------------------

@dataclass
class PendingApproval:
    id: str
    original_email_id: str
    original_from: str
    original_subject: str
    original_body: str
    original_message_id: str
    classification: str
    proposed_slots: list      # deserialised from JSON
    approval_thread_id: Optional[str]
    status: str
    created_at: str
    resolved_at: Optional[str]


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

@contextmanager
def _db_session():
    """Transactional SQLite session with WAL mode and busy-timeout."""
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    with _db_session() as conn:
        conn.execute(_CREATE_PROCESSED_EMAILS)
        conn.execute(_CREATE_PENDING_APPROVALS)
        conn.execute(_CREATE_AUDIT_LOG)


# ---------------------------------------------------------------------------
# Processed emails
# ---------------------------------------------------------------------------

def is_processed(email_id: str) -> bool:
    with _db_session() as conn:
        return conn.execute(
            "SELECT 1 FROM processed_emails WHERE email_id = ?", (email_id,)
        ).fetchone() is not None


def mark_processed(
    email_id: str,
    label: str,
    is_urgent: bool,
    summary: str,
    action_taken: str,
) -> None:
    with _db_session() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO processed_emails
               (email_id, label, is_urgent, summary, action_taken)
               VALUES (?, ?, ?, ?, ?)""",
            (email_id, label, int(is_urgent), summary, action_taken),
        )


# ---------------------------------------------------------------------------
# Pending approvals
# ---------------------------------------------------------------------------

def create_pending_approval(
    original_email_id: str,
    original_from: str,
    original_subject: str,
    original_body: str,
    original_message_id: str,
    classification: str,
    proposed_slots: list,
) -> str:
    approval_id = str(uuid.uuid4())
    with _db_session() as conn:
        conn.execute(
            """INSERT INTO pending_approvals
               (id, original_email_id, original_from, original_subject,
                original_body, original_message_id, classification, proposed_slots)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                approval_id, original_email_id, original_from, original_subject,
                original_body, original_message_id, classification,
                json.dumps(proposed_slots),
            ),
        )
    return approval_id


def update_approval_thread(approval_id: str, thread_id: str) -> None:
    with _db_session() as conn:
        conn.execute(
            "UPDATE pending_approvals SET approval_thread_id = ? WHERE id = ?",
            (thread_id, approval_id),
        )


def get_pending_by_thread(thread_id: str) -> Optional[PendingApproval]:
    with _db_session() as conn:
        row = conn.execute(
            """SELECT * FROM pending_approvals
               WHERE approval_thread_id = ? AND status = 'PENDING'""",
            (thread_id,),
        ).fetchone()

    if row is None:
        return None

    return PendingApproval(
        id=row["id"],
        original_email_id=row["original_email_id"],
        original_from=row["original_from"],
        original_subject=row["original_subject"],
        original_body=row["original_body"],
        original_message_id=row["original_message_id"],
        classification=row["classification"],
        proposed_slots=json.loads(row["proposed_slots"] or "[]"),
        approval_thread_id=row["approval_thread_id"],
        status=row["status"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


def resolve_approval(approval_id: str, status: str) -> None:
    with _db_session() as conn:
        conn.execute(
            """UPDATE pending_approvals
               SET status = ?, resolved_at = datetime('now')
               WHERE id = ?""",
            (status, approval_id),
        )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def log_event(email_id: str, event_type: str, details: dict) -> None:
    with _db_session() as conn:
        conn.execute(
            "INSERT INTO audit_log (email_id, event_type, details) VALUES (?, ?, ?)",
            (email_id, event_type, json.dumps(details)),
        )
