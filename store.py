"""User corrections, kept in the same SQLite file as the samples but in separate tables.

Source samples (`events`) are never modified; edits and report reviews sit beside them,
so a correction can always be undone and the original evidence stays intact.
"""
import sqlite3
import time

import logger

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_edits(
  session_id TEXT PRIMARY KEY,
  label TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  unclassified INTEGER NOT NULL DEFAULT 0,
  updated_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS report_review(
  day TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('confirm','reject','edit')),
  text TEXT NOT NULL DEFAULT '',
  updated_ts INTEGER NOT NULL,
  PRIMARY KEY(day, claim_id));
"""
MAX_LABEL, MAX_NOTE = 120, 1000
ACTIONS = ("confirm", "reject", "edit")


def connect(db):
    conn = logger.init_db(db)  # events table
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _clip(value, n):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("expected text")
    return value.strip()[:n]


def save_edit(db, session_id, label="", note="", unclassified=False):
    """Create/replace the correction for one session. An all-empty edit removes it."""
    if not isinstance(session_id, str) or not session_id.startswith("s_") or len(session_id) > 40:
        raise ValueError("bad session id")
    label, note = _clip(label, MAX_LABEL), _clip(note, MAX_NOTE)
    conn = connect(db)
    try:
        if not (label or note or unclassified):
            conn.execute("DELETE FROM session_edits WHERE session_id=?", (session_id,))
        else:
            conn.execute(
                "INSERT INTO session_edits(session_id,label,note,unclassified,updated_ts) VALUES(?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET label=excluded.label, note=excluded.note, "
                "unclassified=excluded.unclassified, updated_ts=excluded.updated_ts",
                (session_id, label, note, 1 if unclassified else 0, int(time.time())))
        conn.commit()
    finally:
        conn.close()


def load_edits(db):
    conn = connect(db)
    try:
        rows = conn.execute("SELECT session_id,label,note,unclassified FROM session_edits").fetchall()
    finally:
        conn.close()
    return {r[0]: {"label": r[1], "note": r[2], "unclassified": bool(r[3])} for r in rows}


def save_review(db, day, claim_id, action, text=""):
    if action not in ACTIONS:
        raise ValueError("bad action")
    if not isinstance(claim_id, str) or not claim_id.startswith("c_") or len(claim_id) > 40:
        raise ValueError("bad claim id")
    text = _clip(text, MAX_NOTE)
    if action == "edit" and not text:
        raise ValueError("edited text is empty")
    conn = connect(db)
    try:
        conn.execute(
            "INSERT INTO report_review(day,claim_id,action,text,updated_ts) VALUES(?,?,?,?,?) "
            "ON CONFLICT(day,claim_id) DO UPDATE SET action=excluded.action, text=excluded.text, "
            "updated_ts=excluded.updated_ts",
            (day, claim_id, action, text if action == "edit" else "", int(time.time())))
        conn.commit()
    finally:
        conn.close()


def clear_review(db, day, claim_id):
    conn = connect(db)
    try:
        conn.execute("DELETE FROM report_review WHERE day=? AND claim_id=?", (day, claim_id))
        conn.commit()
    finally:
        conn.close()


def load_review(db, day):
    conn = connect(db)
    try:
        rows = conn.execute("SELECT claim_id,action,text FROM report_review WHERE day=?", (day,)).fetchall()
    finally:
        conn.close()
    return {r[0]: {"action": r[1], "text": r[2]} for r in rows}


def last_sample_ts(db):
    conn = connect(db)
    try:
        row = conn.execute("SELECT MAX(ts) FROM events").fetchone()
    finally:
        conn.close()
    return row[0]
