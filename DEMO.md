# Demo script (about 2 minutes)

Use `python server.py --sample` for a repeatable demo. Say on camera that the data is sample data. For a real-data
demo, record only after you have verified Windows tracking (README, "Manual verification").

1. **Open the dashboard** at http://127.0.0.1:8765/. Point at the SAMPLE DATA banner (or, with real data, the
   "Tracking appears active" line), the tracked time, session count and the per-app bars.
2. **Review sessions.** Scroll the timeline. Open "Evidence and edit" on the first session, show the stored
   sample count and the estimate note, type a label ("Hirune core"), Save. Tick "Leave unclassified" on one
   session to show unknown activity stays unknown.
3. **Inspect evidence.** Show the "Recorded facts" and "Your descriptions" sections: each line is tagged
   recorded / you, and your label appears as yours.
4. **Generate and review the report.**
   - Without a model: click **Generate AI summary**. If Ollama is not running, point at the honest
     "AI summary unavailable" message and that the report is still complete.
   - With Ollama running: AI sentences appear tagged "AI · unverified" with their evidence; show a flagged claim
     (cites missing evidence or states a duration) under "Flagged and not included". Press **Confirm** on one,
     **Reject** on one, and rewrite one with **Save my wording**.
5. **Export.** Click **Export CSV**, open it, and show the sample row whose window title begins with `'=HYPERLINK`:
   the leading apostrophe is the formula-injection protection. Show the `origin` and `status` columns.

Best recording: steps 1, 2, 4 (with Ollama) and 5 in one take. Close with the README's privacy section.
