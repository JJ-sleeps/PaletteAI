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
from .client import VibeCheckClient, is_own_rescore, parse_event

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
# VibeCheck draws a Panel: {title, summary?, metrics?: [{label, value, unit?}], items?: [{text, ok?}],
# chart?: {type, title?, x_label?, y_label?, points: [{label, value}]}} (backend/vibe/schemas.py).
def _fallback(message: str, palette: list[str] | None = None) -> dict:
    panel = {"title": TITLE, "summary": message}
    if palette:
        panel["items"] = [{"text": "Colours in this check: " + ", ".join(palette)}]
    return panel


def build_panel(info: dict, fix: dict, rescored: dict | None, rescore_error: str | None) -> dict:
    swaps = fix["swaps"]
    if not swaps:
        summary = ("Every main colour is already close to your team's palette. No changes suggested."
                   if not fix["mismatches"] else "Some colours are off-brand, but no team colour can replace them without hurting readability.")
    else:
        summary = (f"{len(swaps)} colour swap{'s' if len(swaps) > 1 else ''} would bring this closer to your team's palette "
                   "while keeping text readable. These are suggestions; your design is not changed.")

    metrics: list[dict] = []
    original = info.get("score")
    if original is not None:
        metrics.append({"label": "VibeCheck score now", "value": original})
    if swaps and rescored and rescored.get("score") is not None:
        metrics.append({"label": "Score with these swaps", "value": rescored["score"]})
    metrics.append({"label": "Colours swapped", "value": f"{len(swaps)} of {len(fix['original'])}"})
    c = fix["contrast"]
    if c:
        metrics.append({"label": "Text contrast after", "value": c["after"], "unit": ":1"})

    items: list[dict] = []
    for reason in info.get("colour_reasons", [])[:1]:
        items.append({"ok": False, "text": f"VibeCheck: {reason}"})
    for s in swaps:
        items.append({"text": f"Replace {s['from']} with {s['to']} ({s['kind']}): {s['delta_e_before']} -> {s['delta_e_after']} "
                              "ΔE from the team palette."})
    for s in fix["skipped"]:
        items.append({"ok": False, "text": f"{s['color']}: {s['reason']}"})
    if c:
        items.append({"ok": c["passes_aa"],
                      "text": f"Text {c['new_foreground']} on {c['new_background']}: {c['before']}:1 before, {c['after']}:1 after "
                              f"({'passes' if c['passes_aa'] else 'fails'} WCAG AA, 4.5:1)."})
    if swaps:
        if rescored and rescored.get("score") is not None:
            if isinstance(original, (int, float)):
                diff = round(rescored["score"] - original, 1)
                word = "higher" if diff > 0 else "lower" if diff < 0 else "no change"
                change = f"{abs(diff):g} points {word}" if diff else word
            else:
                change = "see score"
            where = f" (saved as check #{rescored['check_id']} in History)" if rescored.get("check_id") else ""
            items.append({"ok": None if not isinstance(original, (int, float)) else rescored["score"] >= original,
                          "text": f"VibeCheck re-scored your images recoloured with these swaps: {change}{where}. "
                                  "This is VibeCheck's own score, not our prediction."})
        else:
            items.append({"ok": None, "text": f"No re-score: {rescore_error or 'VibeCheck returned no score'}."})

    panel = {"title": TITLE, "summary": summary, "metrics": metrics, "items": items}
    if swaps:
        panel["chart"] = {"type": "bar", "title": "Average distance from the team palette (lower is closer)",
                          "y_label": "ΔE (CIEDE2000)",
                          "points": [{"label": "Now", "value": fix["mean_delta_e_before"]},
                                     {"label": "With swaps", "value": fix["mean_delta_e_after"]}]}
    return panel


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


HOST_LIMIT_S = 4.5     # VibeCheck waits 5 s for a plugin; leave a margin


def handle_event(event: dict, team_palette=None, rescore=None) -> dict:
    """The plugin itself: ``check.completed`` event in, result panel out.

    In-app mode (no separate server): the host imports this module and calls
    ``handle_event(event, team_palette, rescore)`` with its own functions
    ``team_palette(team_id) -> colours`` and ``rescore(team_id, palette, check_id) -> {"score": n}``.
    When those are omitted, VibeCheck's REST API at ``event["api_base"]`` is used instead.
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
    if is_own_rescore(info):          # the check we submitted ourselves to re-score: don't recurse
        return done("own_rescore", _fallback("This check is Palette Fixer's re-score of a recoloured image."))
    if not plan:
        return done("no_palette", _fallback("This check has no colours to work with, so there is nothing to fix."))
    if not info["team_id"] or (team_palette is None and not info["api_base"]):
        return done("no_api", _fallback("The event had no api_base or team id, so the team's palette could not be fetched.", plan))

    client = VibeCheckClient(info["api_base"], transport=app.state.transport) if info["api_base"] else None
    get_palette = team_palette or client.team_palette
    try:
        try:
            signature, bad_sig = fixer.clean_palette(get_palette(info["team_id"]))
        except Exception as exc:        # host function or HTTP call failed: degrade, never crash the host
            return done("palette_unavailable", _fallback(
                f"Couldn't get the team's palette from VibeCheck ({type(exc).__name__}), so no suggestions were made.", plan))
        if not signature:
            return done("no_signature", _fallback("This team has no signature palette yet, so there is nothing to compare against.", plan))
        fg, bg = info["foreground"], info["background"]
        if not (fg and bg):
            fg, bg = fixer.infer_roles(plan)
        fg = (fixer.clean_palette([fg])[0] or [None])[0]
        bg = (fixer.clean_palette([bg])[0] or [None])[0]
        fix = fixer.propose_fix(plan, signature, fg, bg, fixer.clean_palette(info["flagged"])[0])
        rescored, error = None, None
        if fix["swaps"]:
            left = HOST_LIMIT_S - (time.perf_counter() - started)
            try:
                if rescore is not None:
                    rescored = rescore(info["team_id"], fix["proposed"], info["check_id"])
                elif client is None:
                    raise RuntimeError("no scoring function")
                elif left < 1.0:
                    raise TimeoutError("not enough time left before VibeCheck's 5-second limit")
                else:
                    rescored = client.rescore(info, fix, timeout=left - 0.5)
            except LookupError as exc:
                error = str(exc)
            except Exception as exc:
                error = f"VibeCheck could not re-score in time ({type(exc).__name__})"
        panel = build_panel(info, fix, rescored, error)
        return done("ok" if not error else "rescore_unavailable", panel, signature=signature,
                    rejected_signature=bad_sig, roles={"foreground": fg, "background": bg}, fix=fix, rescore=rescored)
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8010)))
