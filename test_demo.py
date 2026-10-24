"""Tests for the demo layer: edit store, evidence report, CSV export, dashboard server, sample data."""
import csv
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime

import ai_config
import evidence_report as er
import export
import sample_data
import server
import store
from report_generator import make_blocks
from sessionizer import sessionize

DAY = "2026-10-20"
T0 = int(datetime.strptime(DAY + " 09:00", "%Y-%m-%d %H:%M").timestamp())


def samples(app="Code.exe", title="main.py - Code", n=4, start=T0):
    return [(start + 30 * i, app, title) for i in range(n)]


def make_db(rows):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.db")
    conn = store.connect(path)
    conn.executemany("INSERT OR IGNORE INTO events(ts,app,title) VALUES(?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


class FakeProvider:
    def __init__(self, items, models=("gemma3:4b",)):
        self.items, self.models = items, list(models)

    def list_models(self):
        return self.models

    def chat_json(self, prompt, model):
        return json.dumps({"items": self.items})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db(samples())
        self.sid = sessionize(samples())[0]["id"]

    def test_edit_persists_across_connections(self):
        store.save_edit(self.db, self.sid, "Nap Report", "wrote tests", False)
        self.assertEqual(store.load_edits(self.db)[self.sid],
                         {"label": "Nap Report", "note": "wrote tests", "unclassified": False})

    def test_edit_overwrites_and_empty_edit_removes(self):
        store.save_edit(self.db, self.sid, "A")
        store.save_edit(self.db, self.sid, "B", unclassified=True)
        self.assertEqual(store.load_edits(self.db)[self.sid]["label"], "B")
        store.save_edit(self.db, self.sid, "", "", False)
        self.assertNotIn(self.sid, store.load_edits(self.db))

    def test_unclassified_alone_is_kept(self):
        store.save_edit(self.db, self.sid, unclassified=True)
        self.assertTrue(store.load_edits(self.db)[self.sid]["unclassified"])

    def test_source_samples_untouched_by_edits(self):
        before = store.connect(self.db).execute("SELECT ts,app,title FROM events ORDER BY ts").fetchall()
        store.save_edit(self.db, self.sid, "X", "Y")
        after = store.connect(self.db).execute("SELECT ts,app,title FROM events ORDER BY ts").fetchall()
        self.assertEqual(before, after)

    def test_validation(self):
        for bad in ("", "x_123", None, "s_" + "a" * 60):
            with self.assertRaises(ValueError):
                store.save_edit(self.db, bad, "L")
        with self.assertRaises(ValueError):
            store.save_edit(self.db, self.sid, 123)

    def test_long_text_is_clipped(self):
        store.save_edit(self.db, self.sid, "L" * 500, "N" * 5000)
        e = store.load_edits(self.db)[self.sid]
        self.assertEqual((len(e["label"]), len(e["note"])), (store.MAX_LABEL, store.MAX_NOTE))

    def test_review_roundtrip_and_validation(self):
        store.save_review(self.db, DAY, "c_abc", "confirm")
        store.save_review(self.db, DAY, "c_def", "edit", "my words")
        r = store.load_review(self.db, DAY)
        self.assertEqual(r["c_abc"]["action"], "confirm")
        self.assertEqual(r["c_def"], {"action": "edit", "text": "my words"})
        self.assertEqual(store.load_review(self.db, "2026-01-01"), {})
        with self.assertRaises(ValueError):
            store.save_review(self.db, DAY, "c_x", "approve")
        with self.assertRaises(ValueError):
            store.save_review(self.db, DAY, "c_x", "edit", "  ")
        store.clear_review(self.db, DAY, "c_abc")
        self.assertNotIn("c_abc", store.load_review(self.db, DAY))


class ReportTests(unittest.TestCase):
    EV = samples() + samples("chrome.exe", "Docs - Chrome", 2, T0 + 600)

    def blocks(self):
        return make_blocks(sessionize(self.EV))

    def ai(self, items):
        return (items, "ok", "Model: test")

    def test_report_without_ai_is_complete_and_labelled(self):
        rep = er.build_report(DAY, self.EV)
        self.assertEqual(rep["ai_status"], "off")
        self.assertTrue(rep["recorded"] and all(r["kind"] == "recorded" and r["evidence"] for r in rep["recorded"]))
        self.assertEqual(rep["claims"], [])
        md = er.render_markdown(rep)
        self.assertIn("Recorded", md)
        self.assertNotIn("AI-generated", md)

    def test_total_is_python_computed(self):
        rep = er.build_report(DAY, self.EV)
        self.assertEqual(rep["total_seconds"], sum(s["seconds"] for s in sessionize(self.EV)))

    def test_valid_ai_claim_is_unverified_with_evidence(self):
        b = self.blocks()[0]
        rep = er.build_report(DAY, self.EV, ai=self.ai([{"block_ids": [b["id"]], "summary": "Worked on a Python file."}]))
        c = rep["claims"][0]
        self.assertEqual((c["kind"], c["status"]), ("ai", "unverified"))
        self.assertEqual(c["evidence"], sorted(b["session_ids"]))
        self.assertIn("AI, unverified", er.render_markdown(rep))

    def test_unsupported_claims_flagged_not_included(self):
        b = self.blocks()[0]
        items = [{"block_ids": ["b_nope"], "summary": "Did a thing."},
                 {"block_ids": [], "summary": "No evidence cited."},
                 {"block_ids": [b["id"]], "summary": "Spent 3 hours on it."},
                 {"summary": "malformed"},
                 {"block_ids": [b["id"]], "summary": "  "},
                 {"block_ids": [b["id"]], "summary": "Fine claim."}]
        rep = er.build_report(DAY, self.EV, ai=self.ai(items))
        self.assertEqual([c["original"] for c in rep["claims"]], ["Fine claim."])
        reasons = " | ".join(f["reason"] for f in rep["flagged"])
        for part in ("does not exist", "cites no evidence", "duration", "malformed", "empty"):
            self.assertIn(part, reasons)
        self.assertIn("Flagged", er.render_markdown(rep))

    def test_ai_unavailable_shows_note_and_no_claims(self):
        rep = er.build_report(DAY, self.EV, ai=([], "unavailable", "AI summary unavailable: down"))
        self.assertEqual(rep["claims"], [])
        self.assertIn("unavailable", er.render_markdown(rep))
        self.assertIn("Recorded", er.render_markdown(rep))

    def test_review_confirm_reject_edit(self):
        b = self.blocks()[0]
        item = {"block_ids": [b["id"]], "summary": "Edited a script."}
        cid = er.claim_id(item["block_ids"], item["summary"])
        for action, text, status in (("confirm", "", "confirmed"), ("reject", "", "rejected"), ("edit", "I wrote tests.", "edited")):
            rep = er.build_report(DAY, self.EV, review={cid: {"action": action, "text": text}}, ai=self.ai([item]))
            c = rep["claims"][0]
            self.assertEqual(c["status"], status)
            self.assertEqual(c["evidence"], sorted(b["session_ids"]))  # evidence survives every correction
            self.assertEqual(c["original"], "Edited a script.")
        md = er.render_markdown(rep)
        self.assertIn("edited by you", md)
        self.assertEqual(rep["claims"][0]["kind"], "user")  # an edited sentence is the user's, not the AI's

    def test_rejected_claim_not_in_text(self):
        b = self.blocks()[0]
        item = {"block_ids": [b["id"]], "summary": "Edited a script."}
        cid = er.claim_id(item["block_ids"], item["summary"])
        rep = er.build_report(DAY, self.EV, review={cid: {"action": "reject", "text": ""}}, ai=self.ai([item]))
        self.assertNotIn("Edited a script.", er.render_markdown(rep))

    def test_user_edits_appear_as_user_statements(self):
        sid = sessionize(self.EV)[0]["id"]
        rep = er.build_report(DAY, self.EV, edits={sid: {"label": "Nap Report", "note": "tests", "unclassified": False}})
        self.assertEqual(rep["user"][0]["kind"], "user")
        self.assertIn("Nap Report", rep["user"][0]["text"])
        rep = er.build_report(DAY, self.EV, edits={sid: {"label": "", "note": "", "unclassified": True}})
        self.assertIn("unclassified", rep["user"][0]["text"])

    def test_empty_day(self):
        rep = er.build_report(DAY, [])
        self.assertIn("No activity recorded", er.render_markdown(rep))

    def test_ai_items_ok_and_failures(self):
        blocks = self.blocks()
        items, status, note = er.ai_items(blocks, ai_config.AIConfig(), FakeProvider([{"block_ids": [blocks[0]["id"]], "summary": "x"}]))
        self.assertEqual((status, len(items)), ("ok", 1))
        items, status, note = er.ai_items(blocks, ai_config.AIConfig(), FakeProvider([], models=["llama3"]))
        self.assertEqual((items, status), ([], "unavailable"))
        self.assertIn("not found", note)

    def test_ollama_not_running_is_reported_not_faked(self):
        cfg = ai_config.AIConfig(host="http://127.0.0.1:1", timeout=2)  # nothing listens here
        items, status, note = er.ai_items(self.blocks(), cfg)
        self.assertEqual((items, status), ([], "unavailable"))
        self.assertIn("unreachable", note)

    def test_remote_host_refused(self):
        items, status, note = er.ai_items(self.blocks(), ai_config.AIConfig(host="https://api.example.com"))
        self.assertEqual(status, "unavailable")
        self.assertIn("Refusing non-local", note)


class ExportTests(unittest.TestCase):
    def rows(self, rep):
        return list(csv.DictReader(io.StringIO(export.to_csv(rep))))

    def test_safe_cell(self):
        for bad in ("=1+1", "+cmd", "-2", "@SUM(A1)", "\tx", "\rx", "  =1+1"):
            self.assertTrue(export.safe_cell(bad).startswith("'"), bad)
        self.assertEqual(export.safe_cell("hello = world"), "hello = world")
        self.assertEqual(export.safe_cell(42), 42)

    def test_export_contents_and_injection(self):
        ev = samples(title='=HYPERLINK("http://x.invalid","a")') + samples("Code.exe", "ok", 2, T0 + 900)
        sid = sessionize(ev)[0]["id"]
        rep = er.build_report(DAY, ev, edits={sid: {"label": "@evil", "note": "+n", "unclassified": False}})
        rows = self.rows(rep)
        sess = [r for r in rows if r["row_type"] == "session"]
        self.assertEqual(len(sess), 2)
        self.assertTrue(sess[0]["window_title"].startswith("'="))
        self.assertEqual((sess[0]["label"], sess[0]["note"]), ("'@evil", "'+n"))
        self.assertEqual(sess[0]["duration_seconds"], "120")
        for r in rows:  # no cell anywhere may start with a formula character
            for v in r.values():
                self.assertFalse(v.startswith(("=", "+", "-", "@")), v)

    def test_export_marks_origin_and_excludes_rejected(self):
        ev = samples()
        b = make_blocks(sessionize(ev))[0]
        items = [{"block_ids": [b["id"]], "summary": "Kept."}, {"block_ids": [b["id"]], "summary": "Dropped."}]
        cid = er.claim_id([b["id"]], "Dropped.")
        rep = er.build_report(DAY, ev, review={cid: {"action": "reject", "text": ""}}, ai=(items, "ok", ""))
        texts = [(r["origin"], r["status"], r["text"]) for r in self.rows(rep) if r["row_type"] == "statement"]
        self.assertIn(("ai", "unverified", "Kept."), texts)
        self.assertFalse([t for t in texts if t[2] == "Dropped."])
        self.assertTrue([t for t in texts if t[0] == "recorded"])

    def test_sample_rows_marked(self):
        rep = er.build_report(DAY, samples(), sample=True)
        self.assertEqual(self.rows(rep)[0]["origin"], "sample")

    def test_empty_export_is_header_only(self):
        self.assertEqual(len(self.rows(er.build_report(DAY, []))), 0)


class AppTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db(samples(n=6))
        self.t_last = T0 + 30 * 5
        self.app = server.App(self.db, cfg=ai_config.AIConfig(), now=lambda: self.t_last + 10)

    def get(self, path, **q):
        code, ctype, body, _ = self.app.handle("GET", path, q)
        return code, (json.loads(body) if ctype.startswith("application/json") else body)

    def post(self, path, body):
        code, _, data, _ = self.app.handle("POST", path, {}, body)
        return code, json.loads(data)

    def test_day_view(self):
        code, d = self.get("/api/day", day=DAY)
        self.assertEqual(code, 200)
        self.assertEqual((d["session_count"], d["total_seconds"]), (1, 180))
        self.assertEqual(d["apps"][0]["app"], "Code.exe")

    def test_empty_day(self):
        code, d = self.get("/api/day", day="2026-01-01")
        self.assertEqual((code, d["session_count"], d["total_seconds"], d["apps"]), (200, 0, 0, []))

    def test_bad_day_is_400(self):
        for day in (None, "", "2026-13-40", "yesterday"):
            self.assertEqual(self.get("/api/day", **({"day": day} if day is not None else {}))[0], 400)

    def test_edit_flow_via_api_persists(self):
        sid = self.get("/api/day", day=DAY)[1]["sessions"][0]["id"]
        self.assertEqual(self.post("/api/edit", {"session_id": sid, "label": "Nap Report", "note": "n"})[0], 200)
        fresh = server.App(self.db, cfg=ai_config.AIConfig())  # new app object: must read from SQLite
        s = json.loads(fresh.handle("GET", "/api/day", {"day": DAY})[2])["sessions"][0]
        self.assertEqual((s["label"], s["note"]), ("Nap Report", "n"))

    def test_bad_edit_is_400(self):
        self.assertEqual(self.post("/api/edit", {"session_id": "nope", "label": "x"})[0], 400)
        self.assertEqual(self.post("/api/edit", {})[0], 400)

    def test_review_via_api(self):
        self.assertEqual(self.post("/api/review", {"day": DAY, "claim_id": "c_1", "action": "confirm"})[0], 200)
        self.assertEqual(self.post("/api/review", {"day": DAY, "claim_id": "c_1", "action": "bogus"})[0], 400)
        self.assertEqual(self.post("/api/review", {"day": DAY, "claim_id": "c_1", "action": "clear"})[0], 200)
        self.assertEqual(store.load_review(self.db, DAY), {})

    def test_report_without_ai_then_ai_with_fake_provider(self):
        self.assertEqual(self.get("/api/report", day=DAY)[1]["ai_status"], "off")
        bid = make_blocks(sessionize(samples(n=6)))[0]["id"]
        self.app.provider = FakeProvider([{"block_ids": [bid], "summary": "Edited Python."}])
        code, rep = self.post("/api/ai", {"day": DAY})
        self.assertEqual((code, rep["ai_status"], rep["claims"][0]["status"]), (200, "ok", "unverified"))
        self.assertEqual(self.get("/api/report", day=DAY)[1]["claims"][0]["text"], "Edited Python.")  # cached

    def test_ai_failure_returns_recorded_report(self):
        self.app.cfg = ai_config.AIConfig(host="http://127.0.0.1:1", timeout=2)
        code, rep = self.post("/api/ai", {"day": DAY})
        self.assertEqual((code, rep["ai_status"], rep["claims"]), (200, "unavailable", []))
        self.assertTrue(rep["recorded"])

    def test_ai_on_empty_day(self):
        code, rep = self.post("/api/ai", {"day": "2026-01-01"})
        self.assertEqual((code, rep["claims"]), (200, []))

    def test_csv_endpoint(self):
        code, body = self.get("/export.csv", day=DAY)
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b"row_type,"))

    def test_tracking_states(self):
        self.assertEqual(self.app.tracking()["state"], "active")
        self.app.now = lambda: self.t_last + 3600
        t = self.app.tracking()
        self.assertEqual(t["state"], "off")
        self.assertIn("Not tracking", t["message"])
        empty = server.App(make_db([]), now=lambda: T0)
        self.assertEqual(empty.tracking()["state"], "off")
        self.assertIn("no samples", empty.tracking()["message"])

    def test_sample_mode_never_claims_tracking(self):
        sample = server.App(make_db(samples()), sample=True, now=lambda: T0 + 60)
        t = sample.tracking()
        self.assertEqual(t["state"], "sample")
        self.assertIn("SAMPLE DATA", t["message"])
        self.assertTrue(json.loads(sample.handle("GET", "/api/report", {"day": DAY})[2])["sample"])
        self.assertIn("SAMPLE DATA", json.loads(sample.handle("GET", "/api/report", {"day": DAY})[2])["markdown"])

    def test_unknown_route(self):
        self.assertEqual(self.get("/nope")[0], 404)

    def test_page_served_and_uses_no_external_resources(self):
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertNotIn(b"http://", body.replace(b"http://127", b""))
        self.assertNotIn(b"https://", body)


class SocketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = server.App(make_db(samples()), cfg=ai_config.AIConfig())
        cls.srv = server.make_server(cls.app, 0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        c.putrequest(method, path, skip_host=True)
        for k, v in h.items():
            c.putheader(k, v)
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b"")
        c.putheader("Content-Length", str(len(data)))
        c.endheaders(data)
        r = c.getresponse()
        out = (r.status, r.getheader("Content-Type"), r.read(), dict(r.getheaders()))
        c.close()
        return out

    def test_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_normal_get(self):
        status, ctype, body, headers = self.req("GET", f"/api/day?day={DAY}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["session_count"], 1)
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_foreign_host_rejected(self):
        self.assertEqual(self.req("GET", f"/api/day?day={DAY}", headers={"Host": "evil.example"})[0], 403)

    def test_foreign_origin_rejected_on_get_and_post(self):
        o = {"Origin": "http://evil.example"}
        self.assertEqual(self.req("GET", f"/api/day?day={DAY}", headers=o)[0], 403)
        self.assertEqual(self.req("POST", "/api/ai", {"day": DAY}, {**o, "Content-Type": "application/json"})[0], 403)

    def test_own_origin_accepted(self):
        o = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        self.assertEqual(self.req("POST", "/api/review", {"day": DAY, "claim_id": "c_z", "action": "confirm"}, o)[0], 200)

    def test_post_requires_json(self):
        self.assertEqual(self.req("POST", "/api/edit", b"{}", {"Content-Type": "text/plain"})[0], 415)

    def test_post_bad_json_and_oversize(self):
        h = {"Content-Type": "application/json"}
        self.assertEqual(self.req("POST", "/api/edit", b"{not json", h)[0], 400)
        self.assertEqual(self.req("POST", "/api/edit", b"[]", h)[0], 400)
        self.assertEqual(self.req("POST", "/api/edit", b"x" * (server.MAX_BODY + 1), h)[0], 413)

    def test_csv_download_headers(self):
        status, ctype, body, headers = self.req("GET", f"/export.csv?day={DAY}")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/csv"))
        self.assertIn("attachment", headers.get("Content-Disposition", ""))


class EmptyDbTests(unittest.TestCase):
    def test_missing_events_table_means_no_samples(self):
        from sessionizer import load_events
        import sqlite3
        path = os.path.join(tempfile.mkdtemp(), "empty.db")
        sqlite3.connect(path).close()
        self.assertEqual(load_events(path, 0, 10**10), [])


class SampleDataTests(unittest.TestCase):
    def test_sample_db_is_separate_and_contains_formula_demo(self):
        path = os.path.join(tempfile.mkdtemp(), "s.db")
        day = sample_data.create(path, DAY)
        self.assertEqual(day, DAY)
        app = server.App(path, sample=True)
        view = app.day_view(DAY)
        self.assertGreater(view["session_count"], 3)
        self.assertEqual(view["tracking"]["state"], "sample")
        rows = list(csv.DictReader(io.StringIO(export.to_csv(app.report(DAY)))))
        titles = [r["window_title"] for r in rows if r["row_type"] == "session"]
        self.assertTrue(any(t.startswith("'=HYPERLINK") for t in titles))
        self.assertNotEqual(sample_data.SAMPLE_DB, "nap.db")


if __name__ == "__main__":
    unittest.main()
