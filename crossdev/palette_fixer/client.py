"""Everything that depends on VibeCheck's API lives in this one file.

!! ASSUMED CONTRACT !! VibeCheck's published OpenAPI document was not available
when this was written. The paths and field names below are placeholders taken
from our proposal. When team "rm -rf sleep" publishes their API, adjust the
constants and the three small parse functions here; nothing else should change.
"""
from __future__ import annotations

import os

import httpx

PALETTE_PATH = os.environ.get("VIBECHECK_PALETTE_PATH", "/teams/{team_id}/palette")
SCORE_PATH = os.environ.get("VIBECHECK_SCORE_PATH", "/score")
REGISTER_PATH = os.environ.get("VIBECHECK_REGISTER_PATH", "/plugins")
TIMEOUT = httpx.Timeout(2.0)   # per call; the hook answers within the host's time limit


def parse_event(event: dict) -> dict:
    """Pull what we need out of a ``check.completed`` event, tolerating layout differences."""
    team = event.get("team") or {}
    plan = event.get("plan") or event.get("result") or {}
    failures = event.get("failures") or event.get("failure_reasons") or []
    flagged = [f.get("color") for f in failures if isinstance(f, dict) and f.get("color")]
    return {
        "check_id": str(event.get("check_id") or event.get("id") or ""),
        "team_id": str(team.get("id") or event.get("team_id") or ""),
        "team_name": team.get("name") or "",
        "api_base": (event.get("api_base") or "").rstrip("/"),
        "palette": plan.get("palette") or event.get("palette") or [],
        "foreground": plan.get("foreground") or event.get("foreground"),
        "background": plan.get("background") or event.get("background"),
        "score": event.get("score", plan.get("score")),
        "flagged": flagged,
        "failures": failures,
    }


class VibeCheckClient:
    def __init__(self, api_base: str, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=api_base, timeout=TIMEOUT, transport=transport)

    def close(self) -> None:
        self._http.close()

    def team_palette(self, team_id: str) -> list:
        r = self._http.get(PALETTE_PATH.format(team_id=team_id))
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, list) else body.get("colors") or body.get("palette") or []

    def rescore(self, team_id: str, palette: list[str], check_id: str = "") -> dict:
        r = self._http.post(SCORE_PATH, json={"team_id": team_id, "palette": palette, "check_id": check_id})
        r.raise_for_status()
        body = r.json()
        return {"score": body.get("score"), "raw": body}

    def register(self, hook_url: str) -> dict:
        r = self._http.post(REGISTER_PATH, json={
            "name": "Palette Fixer & Re-Scorer", "hook_url": hook_url, "events": ["check.completed"], "slot": "result"})
        r.raise_for_status()
        return r.json()
