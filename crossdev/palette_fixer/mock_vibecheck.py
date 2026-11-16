"""A stand-in for VibeCheck, so the plugin can be demonstrated before the real one is available.

Run:  python -m crossdev.palette_fixer.mock_vibecheck     (port 8020)
Then: python -m crossdev.palette_fixer.mock_vibecheck --send   (fires a check.completed at the plugin)

It implements only the ASSUMED contract in client.py. It is NOT VibeCheck's
scoring logic: the score here is just "how close is the palette to the
signature palette", enough to exercise the full round trip.
"""
from __future__ import annotations

import json
import sys

import httpx
import uvicorn
from fastapi import FastAPI

from . import fixer

API_BASE = "http://127.0.0.1:8020/api"
SIGNATURE = {"t1": ["#0B3D91", "#F2A900", "#FFFFFF", "#1B1B1B", "#D7263D"]}
SAMPLE_EVENT = {
    "event": "check.completed", "check_id": "demo-1", "api_base": API_BASE,
    "team": {"id": "t1", "name": "Robotics Club"},
    "plan": {"palette": ["#1446A0", "#7BC950", "#FFFFFF", "#222222", "#F7B32B"], "foreground": "#222222", "background": "#FFFFFF"},
    "failures": [{"type": "color_mismatch", "color": "#7BC950", "reason": "Green is not in the team palette."}],
}

app = FastAPI(title="Mock VibeCheck (demo only)")


def score(team_id: str, palette: list[str]) -> int:
    sig = [fixer._lab(s) for s in SIGNATURE.get(team_id, [])]
    if not sig or not palette:
        return 0
    mean = sum(min(fixer.ciede2000(fixer._lab(p), s) for s in sig) for p in palette) / len(palette)
    return max(0, round(100 - 3 * mean))


@app.get("/api/teams/{team_id}/palette")
def team_palette(team_id: str) -> dict:
    return {"team_id": team_id, "colors": SIGNATURE.get(team_id, [])}


@app.post("/api/score")
def rescore(body: dict) -> dict:
    return {"score": score(body.get("team_id", ""), body.get("palette", []))}


if __name__ == "__main__":
    if "--send" in sys.argv:
        event = {**SAMPLE_EVENT, "score": score("t1", SAMPLE_EVENT["plan"]["palette"])}
        print(json.dumps(httpx.post("http://127.0.0.1:8010/hook", json=event, timeout=10).json(), indent=2))
    else:
        uvicorn.run(app, host="127.0.0.1", port=8020)
