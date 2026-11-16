"""Example WEBHOOK plugin: a separate service that contributes a panel to PaletteAI.

1. Start PaletteAI:           python run.py
2. Start this plugin:         python examples/webhook_plugin.py
3. Register it (once):        python examples/webhook_plugin.py --register
   (or use the form on the app's Developers tab with http://127.0.0.1:8001/hook)

PaletteAI then POSTs a PanelRequest to /hook whenever a palette is shown and
renders the Panel returned here inside its own page.
"""
import sys

import httpx
import uvicorn
from fastapi import FastAPI

PALETTEAI = "http://127.0.0.1:8000/api/v1"
app = FastAPI(title="Best text pairs (PaletteAI webhook plugin)")


@app.post("/hook")
def hook(event: dict) -> dict:
    palette = event.get("palette")
    if not palette:
        return {"title": "Best text pairs", "blocks": [{"type": "status", "level": "info", "text": "No palette in this slot."}]}
    pairs = palette["accessibility"]["pairs"][:5]          # already sorted by contrast
    passing = [p for p in pairs if p["aa_normal_text"]]
    blocks = [{
        "type": "status",
        "level": "ok" if passing else "warn",
        "text": f"{len(passing)} of the top {len(pairs)} colour pairs pass WCAG AA for normal text."
                if pairs else "This palette has a single colour, so there are no pairs.",
    }]
    if pairs:
        blocks.append({
            "type": "table",
            "caption": "Highest-contrast pairs in this palette",
            "columns": ["Text", "Background", "Contrast", "AA normal text"],
            "rows": [[p["a_hex"], p["b_hex"], f"{p['ratio']}:1", "Pass" if p["aa_normal_text"] else "Fail"] for p in pairs],
        })
        blocks.append({
            "type": "bars",
            "caption": "Contrast ratio per pair",
            "unit": ":1",
            "items": [{"label": f"{p['a_hex']} on {p['b_hex']}", "value": p["ratio"]} for p in pairs],
        })
    return {"title": "Best text pairs", "blocks": blocks}


if __name__ == "__main__":
    if "--register" in sys.argv:
        r = httpx.post(f"{PALETTEAI}/plugins", json={
            "name": "Best text pairs",
            "description": "Lists the palette's most readable colour pairs.",
            "hook_url": "http://127.0.0.1:8001/hook",
            "slots": ["palette.result"],
        })
        print(r.status_code, r.json())
    else:
        uvicorn.run(app, host="127.0.0.1", port=8001)
