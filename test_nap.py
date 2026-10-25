import os
import sqlite3
import tempfile
import unittest

import logger
import ai_config as ac
import providers
import report_generator as rg
import summarizer
from sessionizer import normalize_title, sessionize


def ev(ts, app="Code", title="main.py"):
    return (ts, app, title)


class SessionizerTests(unittest.TestCase):
    def test_merge_and_duration(self):
        s = sessionize([ev(0), ev(30), ev(60)])
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["seconds"], 90)  # 30+30+30(last sample)

    def test_gap_over_90_splits_and_is_not_counted(self):
        s = sessionize([ev(0), ev(30), ev(500), ev(530)])
        self.assertEqual(len(s), 2)
        self.assertEqual(sum(x["seconds"] for x in s), 120)  # idle gap not counted

    def test_gap_exactly_90_merges(self):
        self.assertEqual(len(sessionize([ev(0), ev(90)])), 1)

    def test_app_switch(self):
        s = sessionize([ev(0), ev(30, "Chrome", "docs"), ev(60)])
        self.assertEqual([x["app"] for x in s], ["Code", "Chrome", "Code"])

    def test_duplicates_out_of_order_and_bad_ts(self):
        s = sessionize([ev(30), ev(0), ev(0), (None, "Code", "x"), ("abc", "Code", "x"), ()])
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["samples"], 2)
        self.assertEqual(s[0]["seconds"], 60)

    def test_no_overlap_double_count(self):
        s = sessionize([ev(0), ev(10, "Chrome", "a"), ev(20)])
        self.assertEqual(sum(x["seconds"] for x in s), 10 + 10 + 30)

    def test_stable_ids(self):
        a = sessionize([ev(0), ev(30)])[0]["id"]
        b = sessionize([ev(30), ev(0)])[0]["id"]
        self.assertEqual(a, b)

    def test_normalize(self):
        self.assertEqual(normalize_title("(3)  Inbox - Mail"), "Inbox - Mail")
        self.assertEqual(normalize_title(None), "")
        self.assertEqual(sessionize([ev(0, title="(2) Inbox"), ev(30, title="(5) Inbox")])[0]["samples"], 2)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.blocks = rg.make_blocks(sessionize([ev(0), ev(30), ev(60, "Chrome", "docs")]))

    def test_totals_in_python(self):
        self.assertEqual({b["app"]: b["seconds"] for b in self.blocks}, {"Code": 60, "Chrome": 30})

    def test_validate_rejects_unknown_ids_and_malformed(self):
        good = self.blocks[0]["id"]
        items = [{"block_ids": [good], "summary": " ok "},
                 {"block_ids": ["b_fake"], "summary": "invented"},
                 {"block_ids": [good, "b_fake"], "summary": "mixed"},
                 {"block_ids": [], "summary": "none"},
                 {"summary": "no ids"}, "junk", {"block_ids": [good], "summary": ""}]
        kept, rejected = rg.validate_highlights(items, self.blocks)
        self.assertEqual(kept, [{"block_ids": [good], "summary": "ok"}])
        self.assertEqual(rejected, 6)

    def test_no_ai_report(self):
        out = rg.generate("2026-01-01", [ev(0), ev(30)], use_ai=False)
        self.assertIn("1m", out)
        self.assertNotIn("INFERRED", out)

    def test_empty_day(self):
        self.assertIn("No activity", rg.generate("d", [], use_ai=False))


class FakeProvider:
    """Proves report/evidence/duration logic is independent of any backend."""
    def __init__(self, models=("gemma3:4b",), reply=None, boom=None):
        self.models, self.reply, self.boom = list(models), reply, boom
        self.prompts = []

    def list_models(self):
        return self.models

    def chat_json(self, prompt, model):
        self.prompts.append(prompt)
        if self.boom:
            raise self.boom
        return self.reply


class ConfigTests(unittest.TestCase):
    def test_defaults_are_local_ollama(self):
        c = ac.load_config({})
        self.assertEqual((c.provider, c.host, c.allow_remote), ("ollama", "http://localhost:11434", False))

    def test_env_overrides(self):
        c = ac.load_config({"NAP_AI_MODEL": "m1", "NAP_AI_TIMEOUT": "5", "NAP_AI_HOST": "http://127.0.0.1:8080"})
        self.assertEqual((c.model, c.timeout, c.host), ("m1", 5, "http://127.0.0.1:8080"))

    def test_non_local_refused_by_default_and_allowed_explicitly(self):
        with self.assertRaises(ac.AIError):
            providers.get_provider(ac.AIConfig(host="https://api.example.com"))
        providers.get_provider(ac.AIConfig(host="https://box.lan", allow_remote=True))

    def test_unknown_provider(self):
        with self.assertRaises(ac.AIError):
            providers.get_provider(ac.AIConfig(provider="nope"))

    def test_pick_model(self):
        c = ac.AIConfig()
        self.assertEqual(ac.pick_model(["llama3:8b", "gemma3:4b"], c), "gemma3:4b")
        self.assertIsNone(ac.pick_model(["llama3:8b"], c))
        self.assertIsNone(ac.pick_model(["gemma3:4b"], ac.AIConfig(model="phi3")))  # no silent swap
        self.assertEqual(ac.pick_model(["llama3:8b"], ac.AIConfig(model="llama3:8b")), "llama3:8b")


class ProviderIndependenceTests(unittest.TestCase):
    EV = [ev(0), ev(30)]

    def _block_id(self):
        return rg.make_blocks(sessionize(self.EV))[0]["id"]

    def test_valid_reply_rendered_with_evidence(self):
        bid = self._block_id()
        fp = FakeProvider(reply='{"items":[{"block_ids":["%s"],"summary":"Edited code."}]}' % bid)
        out = rg.generate("d", self.EV, provider=fp)
        self.assertIn("Edited code.", out)
        self.assertIn(bid, out)

    def test_invented_evidence_dropped(self):
        fp = FakeProvider(reply='{"items":[{"block_ids":["b_fake"],"summary":"Shipped v2."}]}')
        out = rg.generate("d", self.EV, provider=fp)
        self.assertNotIn("Shipped v2.", out)
        self.assertIn("1 unsupported", out)

    def test_model_cannot_change_durations(self):
        fp = FakeProvider(reply='{"items":[{"block_ids":["%s"],"summary":"Worked 9 hours."}]}' % self._block_id())
        out = rg.generate("d", self.EV, provider=fp)
        self.assertIn("Estimated active time: 1m", out)

    def test_malformed_json_falls_back(self):
        out = rg.generate("d", self.EV, provider=FakeProvider(reply="not json"))
        self.assertIn("malformed", out)
        self.assertIn("OBSERVED", out)

    def test_backend_error_falls_back_without_other_provider(self):
        fp = FakeProvider(boom=ac.AIError("down"))
        out = rg.generate("d", self.EV, provider=fp)
        self.assertIn("AI summary unavailable: down", out)

    def test_no_model_installed(self):
        out = rg.generate("d", self.EV, provider=FakeProvider(models=["llama3:8b"]))
        self.assertIn("No suitable model is available", out)

    def test_prompt_has_no_raw_timestamps(self):
        self.assertNotIn("1234567890", summarizer.build_prompt(rg.make_blocks(sessionize([ev(1234567890)]))))


class OllamaProviderTests(unittest.TestCase):
    def test_unreachable_raises_and_report_falls_back(self):
        cfg = ac.AIConfig(host="http://localhost:1", timeout=2)  # nothing listens here
        with self.assertRaises(ac.AIError):
            providers.get_provider(cfg).list_models()
        out = rg.generate("d", [ev(0), ev(30)], cfg=cfg)
        self.assertIn("AI summary unavailable", out)
        self.assertIn("OBSERVED", out)


class LoggerTests(unittest.TestCase):
    def test_run_records_and_survives_failures(self):
        calls = iter([("Code", "a"), RuntimeError("boom"), ("Code", "a")])

        def fake():
            v = next(calls)
            if isinstance(v, Exception):
                raise v
            return v

        t = iter(range(100, 200, 30))
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "t.db")
            n = logger.run(db, 30, get_window=fake, sleep=lambda s: None,
               now=lambda: next(t), max_samples=3)

            read_conn = sqlite3.connect(db)
            try:
                rows = read_conn.execute(
                    "SELECT app, title FROM events ORDER BY ts"
                ).fetchall()
            finally:
                read_conn.close()
        self.assertEqual(n, 3)
        self.assertEqual(rows, [("Code", "a"), ("unknown", ""), ("Code", "a")])

    def test_duplicate_rows_ignored(self):
        conn = logger.init_db(":memory:")
        logger.record(conn, 1, "A", "t")
        logger.record(conn, 1, "A", "t")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_missing_title_stored_empty(self):
        conn = logger.init_db(":memory:")
        logger.record(conn, 1, None, None)
        self.assertEqual(conn.execute("SELECT app, title FROM events").fetchone(), ("unknown", ""))


if __name__ == "__main__":
    unittest.main()


class OllamaCloudConfigTests(unittest.TestCase):
    def test_shilpilist_environment_names_are_supported(self):
        c = ac.load_config({"LLM_API_KEY": "test-secret", "NAP_AI_PROVIDER": "ollama_cloud",
                            "NAP_AI_HOST": "https://ollama.com/v1", "NAP_AI_MODEL": "gemma4:31b"})
        self.assertEqual(c.api_key, "test-secret")
        self.assertEqual(c.model, "gemma4:31b")
        self.assertEqual(c.provider, "ollama_cloud")

    def test_nap_key_takes_precedence(self):
        c = ac.load_config({"LLM_API_KEY": "old", "NAP_AI_API_KEY": "new"})
        self.assertEqual(c.api_key, "new")

    def test_cloud_requires_key_and_pinned_https_host(self):
        with self.assertRaises(ac.AIError):
            providers.get_provider(ac.AIConfig(provider="ollama_cloud", host="https://ollama.com/v1"))
        with self.assertRaises(ac.AIError):
            providers.get_provider(ac.AIConfig(provider="ollama_cloud", host="https://example.com/v1", api_key="x"))