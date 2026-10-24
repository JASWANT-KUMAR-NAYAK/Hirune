"""Evidence-backed daily report.

Every statement carries a KIND so a reader can tell where it came from:
  recorded  computed in Python from stored window samples (still an estimate of activity time)
  user      typed by the user (a session label/note, or an edited AI sentence)
  ai        written by the local model; unverified unless the user explicitly confirms it
and a list of evidence references (session ids / block ids) pointing at stored samples.
The report is complete without AI: AI claims are an optional extra layer.
"""
import hashlib
import re
from datetime import datetime

import ai_config
import providers
import summarizer
from report_generator import make_blocks
from sessionizer import sessionize

# The model is told not to output durations; flag any that slip through (it cannot know them).
_DURATION = re.compile(r"\b\d+(\.\d+)?\s*(h|hr|hrs|hours?|m|min|mins|minutes?)\b", re.I)


def fmt_duration(seconds):
    m = round(seconds / 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def claim_id(block_ids, summary):
    key = "|".join(sorted(block_ids)) + "|" + summary.strip()
    return "c_" + hashlib.sha1(key.encode()).hexdigest()[:10]


def partition_items(items, blocks):
    """Split raw model items into (kept, flagged). Flagged items say why they were not trusted."""
    known, kept, flagged = {b["id"] for b in blocks}, [], []
    for it in items:
        try:
            ids, text = it["block_ids"], it["summary"]
        except (KeyError, TypeError):
            flagged.append({"text": str(it)[:200], "reason": "malformed item"})
            continue
        if not (isinstance(text, str) and text.strip()):
            flagged.append({"text": str(text)[:200], "reason": "empty summary"})
        elif not (isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids)):
            flagged.append({"text": text.strip()[:200], "reason": "cites no evidence"})
        elif not all(i in known for i in ids):
            flagged.append({"text": text.strip()[:200], "reason": "cites evidence that does not exist"})
        elif _DURATION.search(text):
            flagged.append({"text": text.strip()[:200], "reason": "states a duration the model cannot know"})
        else:
            kept.append({"block_ids": ids, "summary": text.strip()})
    return kept, flagged


def ai_items(blocks, cfg=None, provider=None):
    """Ask the local model. Returns (items, status, note); status is 'ok' or 'unavailable'.
    Any failure yields no items and an honest note: a model reply is never invented."""
    cfg = cfg or ai_config.load_config()
    try:
        provider = provider or providers.get_provider(cfg)
        model = ai_config.pick_model(provider.list_models(), cfg)
        if not model:
            want = cfg.model or f"a model starting with '{cfg.model_prefix}'"
            return [], "unavailable", f"Local model not found: need {want}. Install it (e.g. `ollama pull`) or set NAP_AI_MODEL."
        return summarizer.summarize(provider, model, blocks), "ok", f"Model: {model}"
    except ai_config.AIError as e:
        return [], "unavailable", f"AI summary unavailable: {e}"


def build_report(day, events, edits=None, review=None, ai=None, sample=False):
    """ai = (raw_items, status, note) from ai_items(), or None when AI was not requested."""
    edits, review = edits or {}, review or {}
    sessions = sessionize(events)
    for s in sessions:
        e = edits.get(s["id"], {})
        s["label"], s["note"], s["unclassified"] = e.get("label", ""), e.get("note", ""), e.get("unclassified", False)
    blocks = make_blocks(sessions)
    by_id = {s["id"]: s for s in sessions}
    total = sum(s["seconds"] for s in sessions)

    recorded = []
    if sessions:
        recorded.append({"kind": "recorded", "text": f"Estimated active time: {fmt_duration(total)} across {len(sessions)} sessions.",
                         "evidence": [s["id"] for s in sessions]})
    for b in blocks:
        recorded.append({"kind": "recorded", "block_id": b["id"],
                         "text": f"{b['app']}: {fmt_duration(b['seconds'])} in {len(b['session_ids'])} session(s).",
                         "evidence": b["session_ids"]})

    user = []
    for s in sessions:
        if s["label"] or s["note"] or s["unclassified"]:
            parts = []
            if s["label"]:
                parts.append(f"labelled \"{s['label']}\"")
            if s["unclassified"]:
                parts.append("left unclassified")
            if s["note"]:
                parts.append(f"note: {s['note']}")
            user.append({"kind": "user", "session_id": s["id"], "evidence": [s["id"]],
                         "text": f"{datetime.fromtimestamp(s['start']):%H:%M} {s['app']}: " + "; ".join(parts) + "."})

    claims, flagged = [], []
    ai_status, ai_note = "off", None
    if ai is not None:
        raw, ai_status, ai_note = ai
        kept, flagged = partition_items(raw, blocks)
        blocks_by_id = {b["id"]: b for b in blocks}
        for it in kept:
            cid = claim_id(it["block_ids"], it["summary"])
            r = review.get(cid, {})
            action = r.get("action")
            claims.append({
                "kind": "user" if action == "edit" else "ai", "claim_id": cid,
                "status": {"confirm": "confirmed", "edit": "edited", "reject": "rejected"}.get(action, "unverified"),
                "text": r["text"] if action == "edit" else it["summary"],
                "original": it["summary"], "block_ids": it["block_ids"],
                "evidence": sorted({sid for bid in it["block_ids"] for sid in blocks_by_id[bid]["session_ids"]}),
            })
    return {"day": day, "sample": sample, "total_seconds": total, "sessions": sessions, "blocks": blocks,
            "recorded": recorded, "user": user, "claims": claims, "flagged": flagged,
            "ai_status": ai_status, "ai_note": ai_note, "session_index": sorted(by_id)}


def render_markdown(rep):
    """Plain-text report for review/export. Rejected claims are left out; unverified AI is labelled."""
    out = [f"# Nap Report - {rep['day']}"]
    if rep["sample"]:
        out += ["", "> SAMPLE DATA - not real tracking."]
    if not rep["sessions"]:
        return "\n".join(out + ["", "No activity recorded."])
    out += ["", "## Recorded (computed from window samples; estimates, not proof of attention)"]
    out += [f"- {r['text']}  [{', '.join(r['evidence'][:3])}{'...' if len(r['evidence']) > 3 else ''}]" for r in rep["recorded"]]
    if rep["user"]:
        out += ["", "## Your descriptions"] + [f"- {u['text']}  [{u['session_id']}]" for u in rep["user"]]
    shown = [c for c in rep["claims"] if c["status"] != "rejected"]
    if shown:
        out += ["", "## AI-generated summary (local model; unverified unless marked)"]
        for c in shown:
            tag = {"unverified": "AI, unverified", "confirmed": "AI, confirmed by you", "edited": "edited by you"}[c["status"]]
            out.append(f"- {c['text']}  ({tag}; evidence: {', '.join(c['block_ids'])})")
    if rep["ai_status"] == "unavailable":
        out += ["", f"_{rep['ai_note']} Showing recorded activity only._"]
    if rep["flagged"]:
        out += ["", "## Flagged and not included"] + [f"- \"{f['text']}\" - {f['reason']}" for f in rep["flagged"]]
    return "\n".join(out)
