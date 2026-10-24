"""Generate SAMPLE activity so the dashboard can be demoed without real tracking.

Everything here is invented. It is written to its own file (sample.db), never to nap.db, and the
dashboard shows a SAMPLE DATA banner whenever it is used. One window title is a deliberate
spreadsheet-formula string to demonstrate CSV export protection.
"""
import os
from datetime import datetime

import logger

SAMPLE_DB = "sample.db"
INTERVAL = 30

# (start "HH:MM", end "HH:MM", app, window title)
SEGMENTS = [
    ("09:00", "09:40", "Code.exe", "sessionizer.py - nap-report - Visual Studio Code"),
    ("09:40", "09:55", "chrome.exe", "sqlite3 - Python docs - Google Chrome"),
    ("09:55", "10:30", "Code.exe", "evidence_report.py - nap-report - Visual Studio Code"),
    ("10:45", "10:55", "WindowsTerminal.exe", "python -m unittest - nap-report"),
    ("10:55", "11:00", "chrome.exe", '=HYPERLINK("http://example.invalid","click") - Inbox - Google Chrome'),
]


def _ts(day, hhmm):
    return int(datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").timestamp())


def create(path=SAMPLE_DB, day=None):
    """Rebuild the sample database for `day` (default today). Returns the day string."""
    day = day or datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(path):
        os.remove(path)
    conn = logger.init_db(path)
    for a, b, app, title in SEGMENTS:
        for ts in range(_ts(day, a), _ts(day, b), INTERVAL):
            logger.record(conn, ts, app, title)
    conn.close()
    return day
