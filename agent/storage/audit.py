"""Audit report for the AI Email & Calendar Agent.

Usage:
    python -m agent.storage.audit              # last 24 hours
    python -m agent.storage.audit --hours 48   # last 48 hours
    python -m agent.storage.audit --limit 30   # show 30 recent events
"""

import argparse
import json
import os
import sqlite3
import sys

from agent.config import Config


def _db_path() -> str:
    """Resolve DB path — falls back to local ./data/agent.db when running outside Docker."""
    path = Config.DB_PATH
    if not os.path.exists(path) and os.path.exists("data/agent.db"):
        return "data/agent.db"
    return path


def _bar(value: int, total: int, width: int = 20) -> str:
    filled = int(width * value / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def run() -> None:
    parser = argparse.ArgumentParser(description="AI Email Agent — Audit Report")
    parser.add_argument("--hours", type=int, default=24, help="Look-back window in hours")
    parser.add_argument("--limit", type=int, default=15, help="Max recent events to show")
    args = parser.parse_args()

    db = _db_path()
    if not os.path.exists(db):
        sys.exit(f"Database not found: {db}\nIs the agent running? Check DB_PATH in .env")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    since_clause = f"datetime('now', '-{args.hours} hours')"

    # ── Header ──────────────────────────────────────────────────────────────
    print()
    print("╔" + "═" * 58 + "╗")
    print("║      AI Email & Calendar Agent — Audit Report          ║")
    print("╠" + "═" * 58 + "╣")
    print(f"║  Window : last {args.hours}h{' ' * (43 - len(str(args.hours)))}║")
    print(f"║  DB     : {db:<48}║")
    print("╚" + "═" * 58 + "╝")

    # ── Email breakdown ──────────────────────────────────────────────────────
    rows = conn.execute(
        f"SELECT label, COUNT(*) n FROM processed_emails "
        f"WHERE processed_at >= {since_clause} GROUP BY label ORDER BY n DESC"
    ).fetchall()
    total = sum(r["n"] for r in rows)

    print(f"\n📊  Emails processed in last {args.hours}h: {total}")
    if rows:
        for r in rows:
            print(f"    {r['label']:<22} {_bar(r['n'], total)}  {r['n']}")
    else:
        print("    None yet.")

    # ── Pending approvals ────────────────────────────────────────────────────
    pending = conn.execute(
        "SELECT * FROM pending_approvals WHERE status = 'PENDING' ORDER BY created_at"
    ).fetchall()
    print(f"\n⏳  Pending approvals: {len(pending)}")
    for p in pending:
        age = p["created_at"][11:16]
        frm = p["original_from"][:35]
        subj = p["original_subject"][:35]
        print(f"    {p['id'][:8]}  {age}  {frm}")
        print(f"             Subject: {subj}")
    if not pending:
        print("    None.")

    # ── Resolved approvals ───────────────────────────────────────────────────
    resolved = conn.execute(
        f"SELECT status, COUNT(*) n FROM pending_approvals "
        f"WHERE status != 'PENDING' AND created_at >= {since_clause} GROUP BY status"
    ).fetchall()
    if resolved:
        print(f"\n✅  Resolved (last {args.hours}h):")
        for r in resolved:
            print(f"    {r['status']:<12}  {r['n']}")

    # ── Recent audit events ──────────────────────────────────────────────────
    events = conn.execute(
        f"SELECT * FROM audit_log WHERE timestamp >= {since_clause} "
        f"ORDER BY id DESC LIMIT {args.limit}"
    ).fetchall()
    print(f"\n📋  Recent events (newest first, limit {args.limit}):")
    if events:
        print(f"    {'TIME':<6}  {'EVENT':<24}  {'LABEL':<18}  ACTION")
        print("    " + "─" * 70)
        for ev in events:
            ts = ev["timestamp"][11:16]
            details = json.loads(ev["details"] or "{}")
            label = details.get("label", "")
            action = details.get("action_taken", "")
            frm = details.get("from", "")
            print(f"    {ts}  {ev['event_type']:<24}  {label:<18}  {action or frm[:20]}")
    else:
        print("    None yet.")

    # ── Errors ───────────────────────────────────────────────────────────────
    errors = conn.execute(
        f"SELECT * FROM audit_log WHERE event_type = 'PROCESSING_ERROR' "
        f"AND timestamp >= {since_clause} ORDER BY id DESC LIMIT 5"
    ).fetchall()
    print(f"\n❌  Errors in last {args.hours}h: {len(errors)}")
    for err in errors:
        details = json.loads(err["details"] or "{}")
        print(f"    {err['timestamp'][11:19]}  {err['email_id'][:12]}  {details.get('error','')[:60]}")
    if not errors:
        print("    None. ✓")

    print()
    conn.close()


if __name__ == "__main__":
    run()
