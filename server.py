"""Local dashboard: standard-library HTTP server bound to 127.0.0.1, JSON API plus one static page.

Security: loopback only; requests whose Host or Origin is not this server's own address get 403 (blocks
DNS-rebinding and cross-site requests); POST bodies must be JSON and are size-limited. No CDN, no telemetry.

Run:  python server.py --db nap.db            (real data)
      python server.py --sample               (clearly labelled sample data; see sample_data.py)
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import ai_config
import evidence_report
import export
import store
from sessionizer import load_events, sessionize
from report_generator import make_blocks

PAGE = Path(__file__).with_name("dashboard.html")
ACTIVE_WITHIN_S = 90  # logger default interval is 30s; 3 missed samples = not tracking
MAX_BODY = 64 * 1024


class BadRequest(Exception):
    pass


def parse_day(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        raise BadRequest("day must be YYYY-MM-DD")


class App:
    """All behaviour lives here so it can be tested without sockets."""

    def __init__(self, db, sample=False, cfg=None, provider=None, now=time.time):
        self.db, self.sample, self.cfg, self.provider, self.now = db, sample, cfg, provider, now
        self.ai_cache = {}  # day -> (raw_items, status, note); in memory only

    def events(self, day):
        start = datetime.strptime(day, "%Y-%m-%d")
        return load_events(self.db, int(start.timestamp()), int((start + timedelta(days=1)).timestamp()))

    def tracking(self):
        if self.sample:
            return {"state": "sample", "message": "SAMPLE DATA - nothing is being tracked."}
        last = store.last_sample_ts(self.db)
        if last is None:
            return {"state": "off", "message": "Not tracking: no samples recorded yet."}
        age = max(0, int(self.now() - last))
        if age <= ACTIVE_WITHIN_S:
            return {"state": "active", "message": f"Tracking appears active (last sample {age}s ago).", "age_s": age}
        return {"state": "off", "message": f"Not tracking: last sample was {evidence_report.fmt_duration(age)} ago.", "age_s": age}

    def day_view(self, day):
        sessions = sessionize(self.events(day))
        edits = store.load_edits(self.db)
        for s in sessions:
            e = edits.get(s["id"], {})
            s["label"], s["note"], s["unclassified"] = e.get("label", ""), e.get("note", ""), e.get("unclassified", False)
        blocks = make_blocks(sessions)
        return {"day": day, "sample": self.sample, "tracking": self.tracking(),
                "total_seconds": sum(s["seconds"] for s in sessions), "session_count": len(sessions),
                "sessions": sessions,
                "apps": [{"app": b["app"], "seconds": b["seconds"], "sessions": len(b["session_ids"])} for b in blocks]}

    def report(self, day):
        rep = evidence_report.build_report(day, self.events(day), store.load_edits(self.db), store.load_review(self.db, day),
                                           self.ai_cache.get(day), sample=self.sample)
        rep["markdown"] = evidence_report.render_markdown(rep)
        return rep

    def run_ai(self, day):
        blocks = make_blocks(sessionize(self.events(day)))
        if not blocks:
            self.ai_cache[day] = ([], "unavailable", "No activity to summarize.")
        else:
            self.ai_cache[day] = evidence_report.ai_items(blocks, self.cfg, self.provider)
        return self.report(day)

    # --- request dispatch: returns (status, content_type, body_bytes, extra_headers) ---
    def handle(self, method, path, query, body=None):
        try:
            if method == "GET" and path == "/":
                return 200, "text/html; charset=utf-8", PAGE.read_bytes(), {}
            if method == "GET" and path == "/api/day":
                return self.js(self.day_view(parse_day(query.get("day"))))
            if method == "GET" and path == "/api/report":
                return self.js(self.report(parse_day(query.get("day"))))
            if method == "GET" and path == "/export.csv":
                day = parse_day(query.get("day"))
                data = export.to_csv(self.report(day)).encode("utf-8")
                return 200, "text/csv; charset=utf-8", data, {"Content-Disposition": f'attachment; filename="nap-report-{day}.csv"'}
            if method == "POST" and path == "/api/edit":
                store.save_edit(self.db, body.get("session_id"), body.get("label", ""), body.get("note", ""),
                                bool(body.get("unclassified")))
                return self.js({"ok": True})
            if method == "POST" and path == "/api/review":
                day = parse_day(body.get("day"))
                if body.get("action") == "clear":
                    store.clear_review(self.db, day, str(body.get("claim_id")))
                else:
                    store.save_review(self.db, day, body.get("claim_id"), body.get("action"), body.get("text", ""))
                return self.js({"ok": True})
            if method == "POST" and path == "/api/ai":
                return self.js(self.run_ai(parse_day(body.get("day"))))
            return self.js({"error": "not found"}, 404)
        except (BadRequest, ValueError, AttributeError, TypeError) as e:
            return self.js({"error": f"bad request: {e}"}, 400)

    @staticmethod
    def js(obj, code=200):
        return code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode("utf-8"), {}


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # never log request paths or activity data
            pass

        def _allowed(self):
            port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            origin = self.headers.get("Origin")
            if self.headers.get("Host") in hosts and (not origin or origin.removeprefix("http://") in hosts):
                return True
            self._send(*app.js({"error": "forbidden"}, 403))
            return False

        def _send(self, code, ctype, data, headers):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self._allowed():
                u = urlparse(self.path)
                self._send(*app.handle("GET", u.path, {k: v[0] for k, v in parse_qs(u.query).items()}))

        def do_POST(self):
            raw_len = self.headers.get("Content-Length") or "0"
            n = int(raw_len) if raw_len.isdigit() else -1
            if not 0 <= n <= MAX_BODY:
                return self._send(*app.js({"error": "bad length"}, 413))
            raw = self.rfile.read(n)  # read before replying, or the connection resets
            if not self._allowed():
                return
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(*app.js({"error": "send JSON"}, 415))
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(*app.js({"error": "bad JSON"}, 400))
            self._send(*app.handle("POST", urlparse(self.path).path, {}, body))

    return Handler


def make_server(app, port=8765):
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))


def main(argv=None):
    p = argparse.ArgumentParser(description="Nap Report local dashboard (127.0.0.1 only).")
    p.add_argument("--db", default="nap.db")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--sample", action="store_true", help="use generated, clearly labelled sample data")
    p.add_argument("--model")
    a = p.parse_args(argv)
    db, sample = a.db, a.sample
    if sample:
        import sample_data
        db = sample_data.SAMPLE_DB
        sample_data.create(db)
    app = App(db, sample=sample, cfg=ai_config.load_config().with_model(a.model))
    srv = make_server(app, a.port)
    print(f"Nap Report dashboard: http://127.0.0.1:{a.port}/  ({'SAMPLE DATA' if sample else db})  Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
