# Hirune

**Privacy-first AI activity reporting for Windows.**

Hirune records which app and window title is in the foreground, groups activity into sessions, and lets you review and correct them. An optional AI provider (local Ollama or authenticated Ollama Cloud) drafts a summary, with every statement labelled by where it came from.

Local mode keeps AI inference on-device. Cloud mode sends the report prompt, including app names and sampled window titles, to Ollama Cloud.

**Core workflow:** Track → Open the dashboard → Review sessions → Generate a report → Review AI statements (confirm / edit / reject) → Export CSV.

## Requirements

- Python 3.10+ (developed on 3.13). No third-party packages: standard library only.
- Windows for real tracking (see Known limitations). The dashboard, report and tests run on any OS.
- Optional AI: local Ollama, or Ollama Cloud with an API key. No local model download is needed for cloud mode.

## Windows setup (PowerShell)

1. Install Python 3.10 or newer from python.org (tick "Add python.exe to PATH"). Only 3.13 has been tested.
2. Extract the Hirune project folder, then open PowerShell in that folder.
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
Optional local AI: install Ollama and pull a model.

Optional Ollama Cloud (PowerShell; no local model download):

```powershell
$env:NAP_AI_PROVIDER = "ollama_cloud"
$env:NAP_AI_HOST = "https://ollama.com/v1"
$env:NAP_AI_MODEL = "gemma4:31b"
$env:NAP_AI_API_KEY = Read-Host "Paste Ollama Cloud API key"
python server.py --db nap.db
```

The key is entered without being written into source code. Keep it private and do not commit it. This reuses ShilpiList's `LLM_API_KEY` name too, if that variable is already set in the same PowerShell session.

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

79 tests covering the core pipeline, dashboard, edits, reports, export and server.

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

## How statements are labelled

- **recorded**: Computed in Python from stored samples. Times are estimates of activity, not proof of attention.
- **you**: Text you entered, including AI sentences you rewrote.
- **AI**: Written by the configured AI provider. Marked *unverified* until you press Confirm.

The AI is instructed not to state durations. Claims that include durations, cite missing evidence blocks, or provide no evidence are flagged and excluded.

## Privacy model

- **Stored locally:** Timestamps, application names, window titles and your edits are stored in a local SQLite database (`nap.db`).
- **Not collected:** Screenshots, keystrokes, clipboard contents, audio or URLs. A browser may still expose a page title through its window title.
- **Sensitive information:** Window titles may contain document names, email subjects or other private information. Review `nap.db` before sharing it.
- **Local dashboard:** The server binds to `127.0.0.1` and rejects requests with foreign `Host` or `Origin` headers. No CDN or telemetry is used.
- **Local AI:** Local Ollama inference stays on the device and uses a loopback connection.
- **Cloud AI:** When you explicitly configure Ollama Cloud, Hirune sends the summarization prompt, including relevant application names and sampled window titles, to `https://ollama.com`. Use cloud mode only after reviewing the information being sent.
- **API key:** The cloud API key is sent through the Authorization header and is not stored in the SQLite database or source files. Keep your `.env` file private and never commit it.
- **AI report storage:** AI summaries are held in memory and are not saved as raw AI output. Your review decisions and edits are stored separately.
- **Evidence integrity:** Original activity samples are not modified. Session edits and report reviews are stored in separate tables (`session_edits`, `report_review`).

## Known limitations

- **Windows capture still requires real-world verification.** The app uses `ctypes`; confirm that foreground app and window-title sampling works correctly during normal Windows use.
- **No idle detection, pause button, or app exclusion list.** Tracking stops with Ctrl+C. The dashboard cannot start or stop tracking; it reports "Tracking appears active" only if a sample was stored in the last 90 seconds.
- Session IDs derive from app, title and start time. A late sample that changes a session's start time can orphan an edit.
- Dashboard times use the browser's time zone, while report text uses the server's time zone. They match when both run on the same machine.
- **Not implemented:** Git integration, floating indicator, correction learning, weekly views and benchmarks.
- **AI provider:** Ollama Cloud has been tested successfully. Local Ollama requires separate verification on the target machine.

## Manual verification still required

Windows tracking:
1. `python logger.py start --db check.db --yes`, switch between a few windows for two minutes, Ctrl+C.
2. `python -c "import sqlite3;print(sqlite3.connect('check.db').execute('select * from events').fetchall())"` should list your real app names and titles.
3. `python server.py --db check.db` and confirm today's sessions match what you did.

### Optional Ollama Cloud setup

Hirune supports Ollama Cloud without downloading a model locally.

1. Create a `.env` file in the project root (the same folder as `server.py`).
2. Add the following configuration:

```dotenv
NAP_AI_PROVIDER=ollama_cloud
NAP_AI_HOST=https://ollama.com/v1
NAP_AI_MODEL=gemma4:31b
NAP_AI_API_KEY=your_ollama_cloud_api_key
```

3. Replace `your_ollama_cloud_api_key` with your actual API key.
4. Save the file and start Hirune:

```powershell
python server.py --db nap.db
```

**Security notes:**
- Keep `.env` private. Never commit it to GitHub or share it publicly.
- Do not paste your API key into screenshots, logs or documentation.
- Cloud mode sends relevant activity information to Ollama Cloud for summarization.
- Local Ollama can be used instead if you prefer on-device inference.

## Environment variables

`NAP_AI_PROVIDER` (`ollama` or `ollama_cloud`, default `ollama`), `NAP_AI_HOST`, `NAP_AI_MODEL`,
`NAP_AI_MODEL_PREFIX` (default `gemma`), `NAP_AI_TIMEOUT` (seconds, default 120), `NAP_AI_API_KEY` (or ShilpiList-compatible `LLM_API_KEY`). Cloud host is pinned to `https://ollama.com/v1`.
