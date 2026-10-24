"""Daily report. All durations/totals are computed here in Python, never by the model.

Project = application name (no project rules yet).
"""
import argparse
import hashlib
import sys
from datetime import datetime, timedelta

import ai_config
import providers
import summarizer
from sessionizer import load_events, sessionize


def make_blocks(sessions):
    """Group sessions by app into blocks with stable IDs and Python-computed totals."""
    by_app = {}
    for s in sessions:
        by_app.setdefault(s["app"], []).append(s)
    blocks = []
    for app, ss in by_app.items():
        titles = {}
        for s in ss:
            titles[s["title"]] = titles.get(s["title"], 0) + s["seconds"]
        blocks.append({
            "id": "b_" + hashlib.sha1(app.encode()).hexdigest()[:8],
            "app": app,
            "seconds": sum(s["seconds"] for s in ss),
            "session_ids": [s["id"] for s in ss],
            "titles": sorted(titles, key=titles.get, reverse=True),
        })
    return sorted(blocks, key=lambda b: -b["seconds"])


def validate_highlights(items, blocks):
    """Keep only well-formed items whose block_ids all exist. Returns (kept, rejected_count)."""
    known, kept = {b["id"] for b in blocks}, []
    for it in items:
        try:
            ids, text = it["block_ids"], it["summary"]
            ok = (isinstance(ids, list) and ids and all(i in known for i in ids)
                  and isinstance(text, str) and text.strip())
        except (KeyError, TypeError):
            ok = False
        if ok:
            kept.append({"block_ids": ids, "summary": text.strip()})
    return kept, len(items) - len(kept)


def _fmt(seconds):
    m = round(seconds / 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def render(day, blocks, highlights=None, note=None):
    total = sum(b["seconds"] for b in blocks)
    lines = [f"Nap Report - {day}", f"Estimated active time: {_fmt(total)}", "",
             "OBSERVED (from window samples; estimates, not proof of attention):"]
    for b in blocks:
        lines.append(f"  - {b['app']}: {_fmt(b['seconds'])}  [{b['id']}]  e.g. {b['titles'][0] or '(no title)'}")
    lines.append("")
    if highlights:
        lines.append("INFERRED (local AI, from window titles; may be wrong):")
        for h in highlights:
            lines.append(f"  - {h['summary']}  (evidence: {', '.join(h['block_ids'])})")
    if note:
        lines.append(note)
    return "\n".join(lines)


def generate(day, events, cfg=None, use_ai=True, provider=None):
    """provider is injectable (tests / alternate backends); default comes from cfg."""
    cfg = cfg or ai_config.load_config()
    blocks = make_blocks(sessionize(events))
    if not blocks:
        return f"Nap Report - {day}\nNo activity recorded."
    if not use_ai:
        return render(day, blocks)
    try:
        provider = provider or providers.get_provider(cfg)
        model = ai_config.pick_model(provider.list_models(), cfg)
        if not model:
            return render(day, blocks, note="No suitable local model installed (configure NAP_AI_MODEL or install one); showing observed activity only.")
        kept, rejected = validate_highlights(summarizer.summarize(provider, model, blocks), blocks)
        note = f"({rejected} unsupported AI item(s) dropped.)" if rejected else None
        return render(day, blocks, kept, note)
    except ai_config.AIError as e:
        return render(day, blocks, note=f"AI summary unavailable: {e}\nShowing observed activity only.")


def main(argv=None):
    p = argparse.ArgumentParser(description="Generate a Nap Report for one day.")
    p.add_argument("--db", default="nap.db")
    p.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    p.add_argument("--model")
    p.add_argument("--no-ai", action="store_true")
    a = p.parse_args(argv)
    start = datetime.strptime(a.day, "%Y-%m-%d")
    events = load_events(a.db, int(start.timestamp()), int((start + timedelta(days=1)).timestamp()))
    print(generate(a.day, events, ai_config.load_config().with_model(a.model), use_ai=not a.no_ai))
    return 0


if __name__ == "__main__":
    sys.exit(main())
