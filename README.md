# Nap Report

A local, privacy-first activity report. It records which app and window title are in the foreground, groups that
into sessions, and lets you review and correct them. An optional local model (Ollama) drafts a summary, and every
statement is labelled by where it came from. Nothing leaves your computer.

**Core workflow:** track -> open the dashboard -> review sessions (correct labels) -> generate a report -> review the
report (confirm / edit / reject AI sentences) -> export CSV.

## Requirements

- Python 3.10+ (developed on 3.13). **No third-party packages**: standard library only.
- Windows for real tracking (see Known limitations). The dashboard, report and tests run on any OS.
- Optional: [Ollama](https://ollama.com) with a model whose name starts with `gemma` (or set `NAP_AI_MODEL`).

## Windows setup (PowerShell)

1. Install Python 3.10 or newer from python.org (tick "Add python.exe to PATH"). Only 3.13 has been tested.
2. Unzip `nap-report.zip`, then open PowerShell in the unzipped `nap-report` folder.
3. Check it works (no install step; there are no packages to install):

```
python --version
python -m unittest -q
python server.py --sample
```

Open http://127.0.0.1:8765/ and look for the red SAMPLE DATA banner. Stop with Ctrl+C.

4. Track real activity: open two PowerShell windows in the same folder.

```
python logger.py start --db nap.db       # window 1: type y to consent; Ctrl+C stops tracking
python server.py --db nap.db             # window 2: open http://127.0.0.1:8765/
```

If `python` is not found, try `py` instead. If port 8765 is busy, add `--port 8800`.
Optional AI summary: install Ollama, run `ollama pull gemma3:4b`, keep it running, then use the dashboard button.

## Run it

Try it with clearly labelled sample data (no tracking, no model needed):

```
python server.py --sample
```

Open http://127.0.0.1:8765/. A red **SAMPLE DATA** banner stays visible.

Real use (Windows):

```
python logger.py start --db nap.db      # asks for consent; Ctrl+C stops it
python server.py --db nap.db            # in a second terminal
```

Command-line report without the dashboard: `python report_generator.py --db nap.db --day 2026-10-23 [--no-ai]`.

## Tests

```
python -m unittest -q
```

76 tests (28 core pipeline in `test_nap.py`, 48 for the dashboard, edits, report, export and server in `test_demo.py`).

## What is implemented and tested

| Feature | Where | Tested |
| --- | --- | --- |
| Window sampling into SQLite (Windows) | `logger.py` | Logic with injected fakes only; real Windows capture **not** tested |
| Sessionizing (gap merge, no double counting) | `sessionizer.py` | Yes |
| Dashboard: tracked time, session count, app breakdown, session timeline, empty/error states | `server.py`, `dashboard.html` | API and server tests; page checked in headless Chromium |
| Session review: label, note, "leave unclassified" persisted in SQLite | `store.py` | Yes |
| Evidence-backed report with recorded / user / AI labels | `evidence_report.py` | Yes |
| Unsupported AI claims flagged, not included | `evidence_report.py` | Yes |
| Report review: confirm, reject, rewrite AI sentences; evidence is kept | `store.py`, dashboard | Yes |
| Report works without AI | `evidence_report.py` | Yes |
| CSV export with formula-injection protection | `export.py` | Yes |
| Tracking state shown (inferred from last stored sample) | `server.py` | Yes |
| Local-only AI (loopback host enforced, no silent fallback) | `ai_config.py`, `providers.py` | Yes, with fake providers and an unreachable host |

### How statements are labelled

- **recorded**: computed in Python from stored samples (times are estimates of activity, not proof of attention).
- **you**: text you typed, including an AI sentence you rewrote.
- **AI**: written by the local model. Marked *unverified* unless you press Confirm. The model is told not to state
  durations; any claim that does, cites a missing block, or cites no evidence is listed under "Flagged" and left out.

## Privacy model

- Stored: timestamp, app name, window title, your edits. All in one local SQLite file (`nap.db`).
- Not collected: screenshots, keystrokes, clipboard, audio, URLs (a browser may still put a page title in the window title).
- **Window titles can contain sensitive text** (document names, email subjects). Review `nap.db` before sharing it.
- The dashboard binds to `127.0.0.1` only and rejects requests with a foreign `Host` or `Origin` header. No CDN, no telemetry.
- The AI host must be loopback unless you set `NAP_AI_ALLOW_REMOTE=1`. Window titles are sent only to that host.
- The server does not log requests. The AI summary is kept in memory only, not saved.
- Samples are never modified; your edits live in separate tables (`session_edits`, `report_review`).

## Known limitations

- **Windows capture is untested on Windows.** It uses `ctypes` and was written without access to a Windows machine.
- **Ollama inference has never been run successfully here.** The provider code is tested only against fakes and a
  closed port (the "Ollama not running" path). Verify manually (below).
- No idle detection, no pause button, no app exclusion list. Tracking stops with Ctrl+C only. The dashboard cannot
  start or stop tracking; it reports "Tracking appears active" only if a sample was stored in the last 90 s.
- Session ids derive from app, title and start time; a late sample that changes a session's start can orphan an edit.
- Times in the dashboard use the browser's time zone; report text uses the server's. They match on one machine.
- Not built: Git integration, floating indicator, correction learning, weekly views, benchmarks.

## Manual verification still required

Windows tracking:
1. `python logger.py start --db check.db --yes`, switch between a few windows for two minutes, Ctrl+C.
2. `python -c "import sqlite3;print(sqlite3.connect('check.db').execute('select * from events').fetchall())"` should list your real app names and titles.
3. `python server.py --db check.db` and confirm today's sessions match what you did.

Ollama:
1. `ollama pull gemma3:4b` (or any model; set `NAP_AI_MODEL` to its exact name).
2. Run the dashboard, click **Generate AI summary**. Success shows `Model: <name>` and AI sentences tagged "AI · unverified".
3. If it fails, the dashboard shows the reason and the recorded report is unchanged.

## Environment variables

`NAP_AI_PROVIDER` (default `ollama`), `NAP_AI_HOST` (default `http://localhost:11434`), `NAP_AI_MODEL`,
`NAP_AI_MODEL_PREFIX` (default `gemma`), `NAP_AI_TIMEOUT` (seconds, default 120), `NAP_AI_ALLOW_REMOTE`.
