"""Palette Fixer engine: propose colour swaps towards a team's signature palette.

Pure functions, no I/O. Colours are compared with CIEDE2000 in CIELAB; a swap
is only kept if it does not damage the palette's main text/background contrast.
"""
from __future__ import annotations

import math
import re

import numpy as np

from paletteai import color as C   # colour conversions + WCAG contrast (our own base-project code)

MISMATCH_DELTA_E = 10.0   # a colour further than this from every signature colour is "off-brand"
MAX_SWAPS = 3             # change a few colours, not the whole palette
AA_NORMAL = 4.5
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def ciede2000(lab1, lab2) -> float:
    """CIEDE2000 colour difference (Sharma, Wu & Dalal 2005), kL = kC = kH = 1."""
    L1, a1, b1 = (float(v) for v in lab1)
    L2, a2, b2 = (float(v) for v in lab2)
    c_mean = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_mean**7 / (c_mean**7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0
    d_l, d_c = L2 - L1, c2p - c1p
    if c1p * c2p == 0:
        d_h_deg, h_mean = 0.0, h1p + h2p
    else:
        d_h_deg = h2p - h1p
        if d_h_deg > 180:
            d_h_deg -= 360
        elif d_h_deg < -180:
            d_h_deg += 360
        if abs(h1p - h2p) <= 180:
            h_mean = (h1p + h2p) / 2
        elif h1p + h2p < 360:
            h_mean = (h1p + h2p + 360) / 2
        else:
            h_mean = (h1p + h2p - 360) / 2
    d_h = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(d_h_deg) / 2)
    l_mean, cp_mean = (L1 + L2) / 2, (c1p + c2p) / 2
    t = (1 - 0.17 * math.cos(math.radians(h_mean - 30)) + 0.24 * math.cos(math.radians(2 * h_mean))
         + 0.32 * math.cos(math.radians(3 * h_mean + 6)) - 0.20 * math.cos(math.radians(4 * h_mean - 63)))
    d_theta = 30 * math.exp(-(((h_mean - 275) / 25) ** 2))
    r_c = 2 * math.sqrt(cp_mean**7 / (cp_mean**7 + 25**7))
    s_l = 1 + 0.015 * (l_mean - 50) ** 2 / math.sqrt(20 + (l_mean - 50) ** 2)
    s_c = 1 + 0.045 * cp_mean
    s_h = 1 + 0.015 * cp_mean * t
    r_t = -math.sin(math.radians(2 * d_theta)) * r_c
    return math.sqrt((d_l / s_l) ** 2 + (d_c / s_c) ** 2 + (d_h / s_h) ** 2 + r_t * (d_c / s_c) * (d_h / s_h))


def clean_palette(values) -> tuple[list[str], list[str]]:
    """Validate HEX values. Returns ``(valid unique colours, rejected inputs)``."""
    good, bad = [], []
    for v in values or []:
        if isinstance(v, dict):
            v = v.get("hex") or v.get("color") or v.get("value")
        if isinstance(v, str) and _HEX.match(v.strip()):
            h = C.normalize_hex(v)
            if h not in good:
                good.append(h)
        else:
            bad.append(str(v))
    return good, bad


def _lab(hex_value: str) -> np.ndarray:
    return C.hex_to_lab(hex_value)


def _contrast(a: str, b: str) -> float:
    return C.contrast_ratio(C.hex_to_rgb(a), C.hex_to_rgb(b))


def key_pair(palette: list[str], foreground: str | None = None, background: str | None = None) -> tuple[int, int] | None:
    """Indices of the palette's text/background pair.

    Uses the roles from the event when given; otherwise the highest-contrast
    pair, which is the pair a design most plausibly uses for text.
    """
    if foreground in palette and background in palette and foreground != background:
        return palette.index(foreground), palette.index(background)
    best, pair = 0.0, None
    for i in range(len(palette)):
        for j in range(i + 1, len(palette)):
            ratio = _contrast(palette[i], palette[j])
            if ratio > best:
                best, pair = ratio, (i, j)
    return pair


def propose_fix(plan: list[str], signature: list[str], foreground: str | None = None,
                background: str | None = None, flagged: list[str] | None = None) -> dict:
    """Suggest up to ``MAX_SWAPS`` replacements that move ``plan`` towards ``signature``."""
    flagged = set(flagged or [])
    sig_labs = [_lab(s) for s in signature]
    analysis = []
    for i, p in enumerate(plan):
        dists = [ciede2000(_lab(p), s) for s in sig_labs]
        order = sorted(range(len(signature)), key=dists.__getitem__)
        analysis.append({"index": i, "color": p, "nearest": signature[order[0]], "delta_e": dists[order[0]],
                         "order": order, "mismatch": dists[order[0]] > MISMATCH_DELTA_E or p in flagged})

    pair = key_pair(plan, foreground, background)
    original_key = _contrast(plan[pair[0]], plan[pair[1]]) if pair else None
    # The bar a fix must clear: never drop below AA if we had it, never get worse if we didn't.
    bar = min(original_key, AA_NORMAL) if original_key else None

    proposed = list(plan)
    swaps, skipped = [], []
    in_use = {a["nearest"] for a in analysis if not a["mismatch"] and a["delta_e"] < 2}   # already on-brand
    for item in sorted((a for a in analysis if a["mismatch"]), key=lambda a: -a["delta_e"]):
        if len(swaps) >= MAX_SWAPS:
            skipped.append({"color": item["color"], "reason": f"Left unchanged: limit of {MAX_SWAPS} swaps per fix."})
            continue
        choice = None
        for s_idx in item["order"]:
            target = signature[s_idx]
            if target in in_use or target in proposed:
                continue                                        # keep the palette's colours distinct
            # 1) the signature colour itself; 2) its hue/chroma at the original lightness (keeps contrast roles)
            lab_t = _lab(target).copy()
            lab_t[0] = _lab(item["color"])[0]
            for candidate, kind in ((target, "signature colour"), (C.rgb_to_hex(C.lab_to_rgb(lab_t)), "signature hue at the original lightness")):
                trial = list(proposed)
                trial[item["index"]] = candidate
                new_key = _contrast(trial[pair[0]], trial[pair[1]]) if pair else None
                if candidate in proposed or (bar is not None and new_key < bar - 1e-9):
                    continue
                choice = (candidate, kind, target, new_key)
                break
            if choice:
                break
        if not choice:
            skipped.append({"color": item["color"], "reason": "No signature colour could replace it without hurting text contrast or duplicating a colour."})
            continue
        candidate, kind, target, new_key = choice
        proposed[item["index"]] = candidate
        in_use.add(target)
        after = min(ciede2000(_lab(candidate), s) for s in sig_labs)
        swaps.append({
            "index": item["index"], "from": item["color"], "to": candidate, "signature": target, "kind": kind,
            "delta_e_before": round(item["delta_e"], 1), "delta_e_after": round(after, 1),
            "explanation": f"{item['color']} was {item['delta_e']:.1f} dE from the nearest signature colour; "
                           f"replaced with {kind} {candidate} ({after:.1f} dE from {target}).",
        })

    final_key = _contrast(proposed[pair[0]], proposed[pair[1]]) if pair else None
    return {
        "original": plan,
        "proposed": proposed,
        "swaps": swaps,
        "skipped": skipped,
        "mismatches": sum(a["mismatch"] for a in analysis),
        "mean_delta_e_before": round(float(np.mean([a["delta_e"] for a in analysis])), 1) if analysis else 0.0,
        "mean_delta_e_after": round(float(np.mean([min(ciede2000(_lab(p), s) for s in sig_labs) for p in proposed])), 1) if proposed else 0.0,
        "contrast": None if not pair else {
            "foreground": plan[pair[0]], "background": plan[pair[1]],
            "new_foreground": proposed[pair[0]], "new_background": proposed[pair[1]],
            "before": int(original_key * 100) / 100, "after": int(final_key * 100) / 100,
            "passes_aa": final_key >= AA_NORMAL,
        },
    }
