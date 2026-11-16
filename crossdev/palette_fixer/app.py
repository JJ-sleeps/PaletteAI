"""Palette Fixer & Re-Scorer: cross-dev plugin for VibeCheck (team "rm -rf sleep").

Run:  python -m crossdev.palette_fixer.app      (serves POST /hook on port 8010)

It receives VibeCheck's ``check.completed`` event, proposes colour swaps towards
the team's signature palette, asks VibeCheck to re-score the proposal and
returns a panel for VibeCheck's result slot. It only recommends; it never edits
a poster. Every run is recorded in a local SQLite file for debugging and demo.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from . import fixer
from .client import VibeCheckClient, parse_event

TITLE = "Palette Fixer & Re-Scorer"
LOG_PATH = Path(os.environ.get("PALETTE_FIXER_DB", Path(__file__).parent / "data" / "fixer_log.db"))

app = FastAPI(title=TITLE, version="1.0.0",
              description="Cross-dev plugin for VibeCheck. POST a check.completed event to /hook.")
app.state.transport = None     # tests inject an httpx transport here


# ---------------------------------------------------------------- local record
def _log(check_id: str, team_id: str, status: str, payload: dict) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(LOG_PATH) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT DEFAULT"
                         " (strftime('%Y-%m-%dT%H:%M:%SZ','now')), check_id TEXT, team_id TEXT, status TEXT, payload TEXT)")
            conn.execute("INSERT INTO runs (check_id, team_id, status, payload) VALUES (?,?,?,?)",
                         (check_id, team_id, status, json.dumps(payload)))
    except sqlite3.Error:
        pass                                   # logging must never break the hook


# ---------------------------------------------------------------- panel building
def _swatches(label: str, colors: list[str]) -> dict:
    return {"type": "swatches", "label": label, "colors": [{"hex": c, "label": c} for c in colors]}


def _fallback(message: str, extra: list | None = None) -> dict:
    return {"title": TITLE, "blocks": [{"type": "status", "level": "warn", "text": message}] + (extra or [])}


def build_panel(info: dict, fix: dict, rescored: dict | None, rescore_error: str | None) -> dict:
    blocks: list[dict] = []
    if not fix["swaps"]:
        level, text = ("ok", "Every colour is already close to the team's signature palette. No changes suggested.") \
            if not fix["mismatches"] else ("warn", "Off-brand colours were found, but no safe replacement exists.")
        blocks.append({"type": "status", "level": level, "text": text})
    else:
        blocks.append({"type": "status", "level": "info",
                       "text": f"{len(fix['swaps'])} colour swap(s) suggested out of {fix['mismatches']} off-brand colour(s). "
                               "These are recommendations; the poster itself is not edited."})
    blocks.append(_swatches("Original palette", fix["original"]))
    if fix["swaps"]:
        blocks.append(_swatches("Suggested palette", fix["proposed"]))
        blocks.append({
            "type": "table", "caption": "Suggested swaps (dE = CIEDE2000 distance to the nearest signature colour; lower is closer)",
            "columns": ["Replace", "With", "dE before", "dE after", "Why"],
            "rows": [[s["from"], s["to"], str(s["delta_e_before"]), str(s["delta_e_after"]), s["explanation"]] for s in fix["swaps"]],
        })
    for s in fix["skipped"]:
        blocks.append({"type": "text", "text": f"{s['color']}: {s['reason']}"})
    c = fix["contrast"]
    if c:
        blocks.append({
            "type": "status", "level": "ok" if c["passes_aa"] else "warn",
            "text": f"Text contrast ({c['new_foreground']} on {c['new_background']}): {c['before']}:1 before, {c['after']}:1 after. "
                    f"{'Passes' if c['passes_aa'] else 'Fails'} WCAG AA for normal text (4.5:1).",
        })
    original = info.get("score")
    blocks.append({"type": "stat", "label": "Original VibeCheck score", "value": "not provided" if original is None else str(original)})
    if fix["swaps"]:
        if rescored and rescored.get("score") is not None:
            blocks.append({"type": "stat", "label": "Score returned for the suggested palette", "value": str(rescored["score"])})
            if isinstance(original, (int, float)) and isinstance(rescored["score"], (int, float)):
                diff = rescored["score"] - original
                word = "higher than" if diff > 0 else "lower than" if diff < 0 else "the same as"
                blocks.append({"type": "text", "text": f"VibeCheck scored the suggested palette {abs(diff):g} point(s) {word} the original. "
                                                       "This is the score VibeCheck returned, not a prediction."})
        else:
            blocks.append({"type": "status", "level": "warn",
                           "text": f"Re-scoring is unavailable ({rescore_error or 'no score returned'}), so no new score is shown."})
    return {"title": TITLE, "blocks": blocks}


# ---------------------------------------------------------------- endpoints
@app.get("/health", summary="Liveness check")
def health() -> dict:
    return {"status": "ok", "plugin": TITLE}


@app.get("/runs", summary="Recent runs recorded locally (debugging / demo)")
def runs(limit: int = 20) -> list[dict]:
    if not LOG_PATH.is_file():
        return []
    with sqlite3.connect(LOG_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]


@app.post("/hook", summary="Receive VibeCheck's check.completed event and return a result panel")
def hook(event: dict) -> dict:
    return handle_event(event)


def handle_event(event: dict, team_palette=None, rescore=None) -> dict:
    """The plugin itself: ``check.completed`` event in, result panel out.

    This is the entry point for running INSIDE the host app (no separate server):
    the host imports this module and calls ``handle_event(event, team_palette, rescore)``
    with its own functions ``team_palette(team_id) -> colours`` and
    ``rescore(team_id, palette, check_id) -> {"score": n}``.
    When those are omitted, the host's REST API at ``event["api_base"]`` is used instead.
    """
    started = time.perf_counter()
    info = parse_event(event)
    plan, rejected = fixer.clean_palette(info["palette"])
    record = {"event": event, "rejected_colors": rejected}

    def done(status: str, panel: dict, **extra) -> dict:
        _log(info["check_id"], info["team_id"], status, {**record, **extra, "panel": panel,
                                                         "elapsed_ms": int((time.perf_counter() - started) * 1000)})
        return panel

    if event.get("event") not in (None, "check.completed"):
        return done("ignored", _fallback(f"Ignored event '{event.get('event')}'. This plugin handles check.completed."))
    if not plan:
        return done("no_palette", _fallback("The check result contained no usable colours, so there is nothing to fix."))
    if not info["team_id"] or (team_palette is None and not info["api_base"]):
        return done("no_api", _fallback("The event had no api_base or team id, so the team's signature palette could not be fetched.",
                                        [_swatches("Original palette", plan)]))

    client = VibeCheckClient(info["api_base"], transport=app.state.transport) if info["api_base"] else None
    get_palette = team_palette or client.team_palette
    do_rescore = rescore or (client.rescore if client else None)
    try:
        try:
            signature, bad_sig = fixer.clean_palette(get_palette(info["team_id"]))
        except Exception as exc:        # host function or HTTP call failed: degrade, never crash the host
            return done("palette_unavailable", _fallback(
                f"The team's signature palette could not be retrieved from VibeCheck ({type(exc).__name__}). No suggestions were made.",
                [_swatches("Original palette", plan)]))
        if not signature:
            return done("no_signature", _fallback("The team has no signature palette yet, so there is nothing to compare against.",
                                                  [_swatches("Original palette", plan)]))
        fg = fixer.clean_palette([info["foreground"]])[0]
        bg = fixer.clean_palette([info["background"]])[0]
        fix = fixer.propose_fix(plan, signature, fg[0] if fg else None, bg[0] if bg else None,
                                fixer.clean_palette(info["flagged"])[0])
        rescored, error = None, None
        if fix["swaps"]:
            try:
                if do_rescore is None:
                    raise RuntimeError("no scoring function")
                rescored = do_rescore(info["team_id"], fix["proposed"], info["check_id"])
            except Exception as exc:
                error = type(exc).__name__
        panel = build_panel(info, fix, rescored, error)
        return done("ok" if not error else "rescore_unavailable", panel, signature=signature,
                    rejected_signature=bad_sig, fix=fix, rescore=rescored)
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8010)))
