"""Turn raw samples into sessions.

NOTE: durations are ESTIMATED activity time (each sample counts for at most one
sampling interval). They are not proof of continuous attention or of any task.
"""
import hashlib
import re
import sqlite3

INTERVAL = 30   # seconds credited per sample, at most
MAX_GAP = 90    # larger gap between samples starts a new session (idle / away)


def normalize_title(title):
    """Conservative: drop leading unread counters like '(3) ' and bullets, collapse spaces."""
    t = re.sub(r"^\s*(\(\d+\)|\[\d+\]|[●•*])\s*", "", title or "")
    return re.sub(r"\s+", " ", t).strip()


def load_events(db, start=None, end=None):
    conn = sqlite3.connect(db)
    q, args = "SELECT ts, app, title FROM events", []
    if start is not None and end is not None:
        q += " WHERE ts >= ? AND ts < ?"
        args = [start, end]
    try:
        rows = conn.execute(q, args).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):  # a brand-new/empty database simply has no samples yet
            raise
        rows = []
    finally:
        conn.close()
    return rows


def _id(prefix, *parts):
    return prefix + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def sessionize(events, interval=INTERVAL, max_gap=MAX_GAP):
    """events: iterable of (ts, app, title). Returns list of session dicts.

    Invalid rows (missing/non-numeric ts) are dropped, exact duplicates removed,
    order fixed. Each sample is credited min(time to next sample, interval), so
    sessions can never overlap or double-count time.
    """
    clean = set()
    for e in events:
        try:
            ts = int(e[0])
        except (TypeError, ValueError, IndexError):
            continue
        clean.add((ts, e[1] or "unknown", normalize_title(e[2])))
    clean = sorted(clean)

    sessions, cur, last_ts = [], None, None
    for i, (ts, app, title) in enumerate(clean):
        nxt = clean[i + 1][0] if i + 1 < len(clean) else None
        credit = interval if nxt is None else min(max(nxt - ts, 0), interval)
        same = (cur and cur["app"] == app and cur["title"] == title
                and ts - last_ts <= max_gap)
        if not same:
            cur = {"app": app, "title": title, "start": ts, "end": ts, "seconds": 0, "samples": 0}
            sessions.append(cur)
        cur["end"] = ts + credit
        cur["seconds"] += credit
        cur["samples"] += 1
        last_ts = ts
    for s in sessions:
        s["id"] = _id("s_", s["app"], s["title"], s["start"])
    return sessions
