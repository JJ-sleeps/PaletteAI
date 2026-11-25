"""A small stand-in for VibeCheck with the SAME API shapes, for tests and offline demos.

Run:  python -m crossdev.palette_fixer.mock_vibecheck          (port 8020)
Then: python -m crossdev.palette_fixer.mock_vibecheck --send   (fires a check.completed at the plugin)

It copies the parts of VibeCheck's contract the plugin uses (github.com/Pro-Nil/vibecheck, backend/vibe/main.py):
the check.completed event, GET /teams/{id}/style-profile, POST /teams/{id}/checks (multipart images) and
/media images. It is NOT VibeCheck's scoring: the score here is just "how close are the image's colours to the
signature palette", enough to exercise the round trip. For the real thing, run VibeCheck itself.
"""
from __future__ import annotations

import io
import json
import sys
from itertools import count

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, Response, UploadFile
from PIL import Image, ImageDraw

from . import fixer

API_BASE = "http://127.0.0.1:8020"
SIGNATURE = {"1": ["#0B3D91", "#F2A900", "#FFFFFF", "#1B1B1B", "#D7263D"]}
_ids = count(100)


def demo_poster() -> Image.Image:
    """White poster, dark text bar, blue block, an off-brand green block and a yellow accent."""
    im = Image.new("RGB", (320, 400), "#FFFFFF")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 320, 120], fill="#1446A0")
    d.rectangle([20, 160, 300, 220], fill="#222222")
    d.rectangle([20, 250, 180, 380], fill="#7BC950")
    d.ellipse([200, 260, 300, 360], fill="#F7B32B")
    return im


SAMPLE_EVENT = {
    "event": "check.completed", "api_base": API_BASE,
    "team": {"id": 1, "name": "Robotics Club"},
    "check": {
        "id": 42, "kind": "plan", "score": 61.0, "verdict": "Borderline", "verdict_level": "borderline",
        "description": "Robotics night poster",
        "reasons": [{"ok": False, "feature": "saturation", "label": "Colour saturation",
                     "text": "Colour saturation: your plan is more saturated than this team's usual posts."}],
        "plan_palette": [{"hex": "#FFFFFF", "share": 0.52}, {"hex": "#1446A0", "share": 0.19},
                         {"hex": "#7BC950", "share": 0.16}, {"hex": "#222222", "share": 0.08}, {"hex": "#F7B32B", "share": 0.05}],
        "inspo_urls": [API_BASE + "/media/demo.png"],
    },
}

app = FastAPI(title="Mock VibeCheck (tests and offline demo only)")


def score_image(team_id: str, img: Image.Image) -> float:
    sig = np.array([fixer._lab(s) for s in SIGNATURE.get(str(team_id), [])])
    if not len(sig):
        return 0.0
    small = np.asarray(img.convert("RGB").resize((24, 30)), dtype=np.float64)
    lab = fixer.C.rgb_to_lab(small).reshape(-1, 3)
    dist = np.sqrt(((lab[:, None, :] - sig) ** 2).sum(-1)).min(1).mean()
    return round(max(0.0, 100 - 2.5 * float(dist)), 1)


@app.get("/teams/{team_id}/style-profile")
def style_profile(team_id: str, include_plugins: bool = True) -> dict:
    sig = SIGNATURE.get(team_id, [])
    return {"signature_palette": [{"hex": h, "share": round(1 / len(sig), 3)} for h in sig] if sig else []}


@app.get("/media/demo.png")
def media() -> Response:
    buf = io.BytesIO()
    demo_poster().save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.post("/teams/{team_id}/checks", status_code=201)
async def create_check(team_id: str, images: list[UploadFile] = File(default=[]), description: str = Form(""),
                       user: str = Form(""), kind: str = Form("plan")) -> dict:
    imgs = [Image.open(io.BytesIO(await f.read())) for f in images]
    s = round(float(np.mean([score_image(team_id, im) for im in imgs])), 1) if imgs else 0.0
    return {"id": next(_ids), "score": s, "verdict": "On-brand" if s >= 60 else "Off-brand",
            "description": description, "user": user, "kind": kind}


if __name__ == "__main__":
    if "--send" in sys.argv:
        event = json.loads(json.dumps(SAMPLE_EVENT))
        event["check"]["score"] = score_image("1", demo_poster())
        print(json.dumps(httpx.post("http://127.0.0.1:8010/hook", json=event, timeout=10).json(), indent=2, ensure_ascii=False))
    else:
        uvicorn.run(app, host="127.0.0.1", port=8020)
