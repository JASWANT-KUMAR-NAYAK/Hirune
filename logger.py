"""Nap Report activity logger.

Records ONLY: timestamp, active app name, active window title -> local SQLite.
No screenshots, audio, keystrokes, page contents or clipboard. Nothing is uploaded.

WARNING: window titles can contain sensitive information (document names,
email subjects, URLs, chat names). Review nap.db before sharing it.

Usage:  python logger.py start [--interval 30] [--db nap.db] [--yes]
Stop:   Ctrl+C (or close the window).
"""
import argparse
import sqlite3
import sys
import time

WARNING = (
    "Nap Report will record the active app name and window title every {n}s "
    "into {db}.\nWindow titles can contain sensitive information. "
    "Data stays on this computer. Press Ctrl+C to stop."
)


def init_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events("
        "ts INTEGER NOT NULL, app TEXT NOT NULL, title TEXT NOT NULL, "
        "UNIQUE(ts, app, title))"
    )
    conn.commit()
    return conn


def record(conn, ts, app, title):
    """Insert one sample. Duplicate (ts, app, title) rows are ignored."""
    conn.execute(
        "INSERT OR IGNORE INTO events(ts, app, title) VALUES (?,?,?)",
        (int(ts), app or "unknown", title or ""),
    )
    conn.commit()


def get_active_window():
    """Return (app, title) of the foreground window. Windows only (ctypes, no extra package)."""
    if sys.platform != "win32":
        raise NotImplementedError("Active-window detection is implemented for Windows only.")
    import ctypes
    from ctypes import wintypes

    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = u32.GetForegroundWindow()
    if not hwnd:
        return "unknown", ""
    buf = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(hwnd, buf, 512)
    title = buf.value
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    app = "unknown"
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        size = wintypes.DWORD(512)
        path = ctypes.create_unicode_buffer(512)
        if k32.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(size)):
            app = path.value.rsplit("\\", 1)[-1]
        k32.CloseHandle(h)
    return app, title


def run(db="nap.db", interval=30, get_window=get_active_window,
        sleep=time.sleep, now=time.time, max_samples=None):
    """Sample until Ctrl+C (or max_samples, used by tests). Returns samples taken."""
    conn = init_db(db)
    n = 0
    try:
        while max_samples is None or n < max_samples:
            try:
                app, title = get_window()
            except NotImplementedError:
                raise
            except Exception:  # a failed sample must not kill tracking
                app, title = "unknown", ""
            record(conn, now(), app, title)
            n += 1
            if max_samples is None or n < max_samples:
                sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()
        print(f"Tracking stopped. {n} samples saved to {db}.")
    return n


def main(argv=None):
    p = argparse.ArgumentParser(description="Nap Report logger (opt-in).")
    p.add_argument("command", choices=["start"])
    p.add_argument("--db", default="nap.db")
    p.add_argument("--interval", type=int, default=30)
    p.add_argument("--yes", action="store_true", help="skip the consent prompt")
    a = p.parse_args(argv)
    print(WARNING.format(n=a.interval, db=a.db))
    if not a.yes and input("Start tracking? [y/N] ").strip().lower() != "y":
        print("Not started.")
        return 1
    run(a.db, a.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
