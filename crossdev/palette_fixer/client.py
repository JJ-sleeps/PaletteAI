"""Everything that depends on VibeCheck's API lives in this one file.

Contract checked against VibeCheck's code (github.com/Pro-Nil/vibecheck, backend/vibe/main.py):

- VibeCheck POSTs ``{"event": "check.completed", "api_base", "team": {id, name}, "check": {id, kind, score,
  verdict, reasons, plan_palette: [{hex, share}], description, inspo_urls, ...}}`` to the plugin's URL.
- The team's signature palette is ``GET /teams/{id}/style-profile`` -> ``signature_palette: [{hex, share}]``.
- VibeCheck scores images, not palettes: ``POST /teams/{id}/checks`` (multipart ``images``, ``description``,
  ``kind``, ``user``). To re-score we recolour the checked images with our swaps and submit those.
- Registration: ``POST /plugins {"name", "url", "slot": "result"}``.
"""
from __future__ import annotations

import io
import os

import httpx
import numpy as np
from PIL import Image

from . import fixer

PALETTE_PATH = os.environ.get("VIBECHECK_PALETTE_PATH", "/teams/{team_id}/style-profile")
CHECKS_PATH = os.environ.get("VIBECHECK_CHECKS_PATH", "/teams/{team_id}/checks")
REGISTER_PATH = os.environ.get("VIBECHECK_REGISTER_PATH", "/plugins")
TIMEOUT = httpx.Timeout(2.0)   # per call; the hook answers within the host's time limit
# Marks the checks this plugin submits for re-scoring, so its own hook can recognise them and not recurse.
RESCORE_TAG = "[palette-fixer re-score]"
RESCORE_USER = "Palette Fixer (re-score)"
MAX_SIDE = 640                 # recoloured images are resized to this, enough for scoring and fast to send
# VibeCheck reason features that are about colour (its other reasons: text amount, busyness, empty space...).
COLOUR_FEATURES = {"warmth", "saturation", "brightness", "contrast", "palette", "colour", "color"}


def parse_event(event: dict) -> dict:
    """Pull what we need out of a ``check.completed`` event, tolerating layout differences."""
    team = event.get("team") or {}
    check = event.get("check") or {}           # VibeCheck: everything about the check is under "check"
    plan = event.get("plan") or event.get("result") or {}
    failures = event.get("failures") or event.get("failure_reasons") or []
    flagged = [f.get("color") for f in failures if isinstance(f, dict) and f.get("color")]
    raw = check.get("plan_palette") or plan.get("palette") or event.get("palette") or []
    # VibeCheck sends [{"hex": "#165faf", "share": 0.87}, ...], largest share first.
    palette = [c.get("hex") if isinstance(c, dict) else c for c in raw]
    reasons = check.get("reasons") or []
    return {
        "check_id": str(check.get("id") or event.get("check_id") or event.get("id") or ""),
        "team_id": str(team.get("id") or event.get("team_id") or ""),
        "team_name": team.get("name") or "",
        "api_base": (event.get("api_base") or "").rstrip("/"),
        "palette": palette,
        "foreground": plan.get("foreground") or event.get("foreground"),
        "background": plan.get("background") or event.get("background"),
        "score": check.get("score", event.get("score", plan.get("score"))),
        "kind": check.get("kind") or "plan",
        "description": check.get("description") or "",
        "inspo_urls": [u for u in check.get("inspo_urls") or [] if isinstance(u, str)],
        "colour_reasons": [r.get("text") for r in reasons
                           if isinstance(r, dict) and r.get("ok") is False and r.get("feature") in COLOUR_FEATURES],
        "flagged": flagged,
        "failures": failures,
    }


def is_own_rescore(info: dict) -> bool:
    return RESCORE_TAG in (info.get("description") or "")


class VibeCheckClient:
    def __init__(self, api_base: str, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=api_base, timeout=TIMEOUT, transport=transport)

    def close(self) -> None:
        self._http.close()

    def team_palette(self, team_id: str) -> list:
        r = self._http.get(PALETTE_PATH.format(team_id=team_id), params={"include_plugins": "false"})
        r.raise_for_status()
        body = r.json()
        pal = body if isinstance(body, list) else body.get("signature_palette") or body.get("colors") or body.get("palette") or []
        return [c.get("hex") if isinstance(c, dict) else c for c in pal]

    def rescore(self, info: dict, fix: dict, timeout: float = 2.5) -> dict:
        """Recolour the checked images with the proposed swaps and ask VibeCheck to score them.

        Returns ``{"score": n, "check_id": id}``. The new check is saved in VibeCheck's History, marked as ours.
        """
        if not info["inspo_urls"]:
            raise LookupError("this check has no images to recolour (description-only plan)")
        files = []
        for i, url in enumerate(info["inspo_urls"][:5]):
            r = self._http.get(url)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            out = Image.fromarray(fixer.recolour(np.asarray(img), fix["original"], fix["proposed"]))
            buf = io.BytesIO()
            out.save(buf, format="PNG")
            files.append(("images", (f"recoloured_{i}.png", buf.getvalue(), "image/png")))
        data = {"description": f"{info['description']} {RESCORE_TAG}".strip(), "user": RESCORE_USER,
                "kind": info["kind"] if info["kind"] in ("plan", "final") else "plan"}
        r = self._http.post(CHECKS_PATH.format(team_id=info["team_id"]), files=files, data=data, timeout=timeout)
        r.raise_for_status()
        body = r.json()
        return {"score": body.get("score"), "check_id": body.get("id"), "raw": {k: body.get(k) for k in ("id", "score", "verdict")}}

    def register(self, hook_url: str) -> dict:
        r = self._http.post(REGISTER_PATH, json={
            "name": "Palette Fixer & Re-Scorer", "url": hook_url, "slot": "result", "author_team": "PaletteAI",
            "description": "Suggests on-brand colour swaps that keep text readable, and re-scores them."})
        r.raise_for_status()
        return r.json()
