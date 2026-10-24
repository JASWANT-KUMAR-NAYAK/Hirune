"""CSV export of reviewed sessions and report statements.

Spreadsheet formula injection: window titles are attacker-influenced text. Any text cell that starts with
= + - @ tab or CR (after optional whitespace) is prefixed with an apostrophe so Excel/Sheets treat it as text.
"""
import csv
import io
from datetime import datetime

COLUMNS = ["row_type", "day", "start", "end", "duration_seconds", "app", "window_title", "label", "note",
           "unclassified", "origin", "status", "text", "evidence"]
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    if not isinstance(value, str):
        return value
    probe = value.lstrip(" ")
    return "'" + value if probe.startswith(_DANGEROUS) else value


def _t(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def to_csv(rep):
    """rep: output of evidence_report.build_report. Rejected AI claims are excluded."""
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    day, origin_note = rep["day"], "sample" if rep["sample"] else "recorded"

    def row(**kw):
        w.writerow({k: safe_cell(v) for k, v in {"day": day, **kw}.items()})

    for s in rep["sessions"]:
        row(row_type="session", start=_t(s["start"]), end=_t(s["end"]), duration_seconds=s["seconds"], app=s["app"],
            window_title=s["title"], label=s["label"], note=s["note"], unclassified="yes" if s["unclassified"] else "no",
            origin=origin_note, evidence=s["id"])
    for r in rep["recorded"]:
        row(row_type="statement", origin="recorded", status="computed", text=r["text"], evidence=" ".join(r["evidence"]))
    for u in rep["user"]:
        row(row_type="statement", origin="user", status="user-provided", text=u["text"], evidence=" ".join(u["evidence"]))
    for c in rep["claims"]:
        if c["status"] != "rejected":
            row(row_type="statement", origin=c["kind"], status=c["status"], text=c["text"], evidence=" ".join(c["evidence"]))
    return buf.getvalue()
