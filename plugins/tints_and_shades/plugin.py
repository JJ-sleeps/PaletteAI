"""Example LOCAL plugin: adds a "Tints & shades" panel and one API route.

This is the integration example for other teams. Copy this folder, rename it,
and edit - PaletteAI loads every ``plugins/<name>/plugin.py`` at start-up.
No core file needs to change.
"""
import colorsys

from fastapi import APIRouter, HTTPException


def _scale(hex_value: str) -> list[dict]:
    r, g, b = (int(hex_value[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, _, s = colorsys.rgb_to_hls(r, g, b)
    out = []
    for step, lightness in zip((100, 300, 500, 700, 900), (0.9, 0.72, 0.5, 0.32, 0.16)):
        rr, gg, bb = colorsys.hls_to_rgb(h, lightness, s)
        out.append({"step": step, "hex": "#%02X%02X%02X" % (round(rr * 255), round(gg * 255), round(bb * 255))})
    return out


def register(host):
    host.describe("Builds a five-step tint/shade scale for each dominant colour.")

    # 1) A panel shown under every extracted palette -------------------------
    def panel(context: dict) -> dict:
        palette = context["palette"]
        blocks = [{"type": "text", "text": "A light-to-dark scale for the three most dominant colours, "
                                           "ready to use as UI colour ramps."}]
        for color in palette["colors"][:3]:
            scale = _scale(color["hex"])
            blocks.append({
                "type": "swatches",
                "label": f"{color['name']} ({color['hex']})",
                "colors": [{"hex": s["hex"], "label": str(s["step"])} for s in scale],
            })
            # host.api gives plugins the core's colour tools without HTTP calls
            report = host.api.contrast(scale[-1]["hex"], scale[0]["hex"])
            ok = report["checks"][0]["passed"]
            blocks.append({
                "type": "status",
                "level": "ok" if ok else "warn",
                "text": f"Step 900 on step 100: contrast {report['ratio']}:1 - "
                        f"{'passes' if ok else 'fails'} WCAG AA for normal text.",
            })
        return {"title": "Tints & shades", "blocks": blocks}

    host.add_panel("palette.result", panel)

    # 2) An extra API route: GET /api/v1/ext/tints_and_shades/palettes/{id} ---
    router = APIRouter()

    @router.get("/palettes/{palette_id}", summary="Tint/shade scales for a stored palette")
    def scales(palette_id: int) -> dict:
        try:
            palette = host.api.get_palette(palette_id)
        except Exception:
            raise HTTPException(404, {"code": "not_found", "message": f"Palette {palette_id} does not exist."})
        return {"palette_id": palette_id, "scales": [{"base": c["hex"], "steps": _scale(c["hex"])} for c in palette["colors"]]}

    host.add_router(router)
