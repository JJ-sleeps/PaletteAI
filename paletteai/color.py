"""Colour science helpers: conversions, WCAG contrast, CVD simulation, harmony.

Everything here is pure (no I/O) so it can be unit-tested and reused by plugins.
"""
from __future__ import annotations

import colorsys
import re

import numpy as np

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_XYZ_TO_RGB = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ]
)
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883])
_EPS = 216 / 24389
_KAPPA = 24389 / 27

# Machado, Oliveira & Fernandes (2009), severity 1.0, applied in linear RGB.
CVD_MATRICES = {
    "protanopia": np.array(
        [
            [0.152286, 1.052583, -0.204868],
            [0.114503, 0.786281, 0.099216],
            [-0.003882, -0.048116, 1.051998],
        ]
    ),
    "deuteranopia": np.array(
        [
            [0.367322, 0.860646, -0.227968],
            [0.280085, 0.672501, 0.047413],
            [-0.011820, 0.042940, 0.968881],
        ]
    ),
    "tritanopia": np.array(
        [
            [1.255528, -0.076749, -0.178779],
            [-0.078411, 0.930809, 0.147602],
            [0.004733, 0.691367, 0.303900],
        ]
    ),
}
CVD_TYPES = tuple(CVD_MATRICES)


# ---------------------------------------------------------------- hex / rgb
def normalize_hex(value: str) -> str:
    """Return ``#RRGGBB`` (upper case) or raise ``ValueError``."""
    m = _HEX_RE.match(value.strip()) if isinstance(value, str) else None
    if not m:
        raise ValueError(f"'{value}' is not a valid HEX colour (use #RGB or #RRGGBB)")
    digits = m.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return "#" + digits.upper()


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = normalize_hex(value)
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def rgb_to_hex(rgb) -> str:
    r, g, b = (int(max(0, min(255, round(float(c))))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


# ---------------------------------------------------------------- sRGB <-> Lab
def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(lin: np.ndarray) -> np.ndarray:
    lin = np.clip(lin, 0.0, 1.0)
    c = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
    return np.clip(np.round(c * 255.0), 0, 255)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (0-255, shape (..., 3)) -> CIELAB (D65)."""
    xyz = srgb_to_linear(rgb) @ _RGB_TO_XYZ.T / _WHITE_D65
    f = np.where(xyz > _EPS, np.cbrt(xyz), (_KAPPA * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """CIELAB (D65) -> sRGB 0-255 floats, clipped to gamut."""
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], axis=-1)
    xyz = np.where(f**3 > _EPS, f**3, (116 * f - 16) / _KAPPA) * _WHITE_D65
    return linear_to_srgb(xyz @ _XYZ_TO_RGB.T)


def hex_to_lab(value: str) -> np.ndarray:
    return rgb_to_lab(np.array(hex_to_rgb(value), dtype=np.float64))


def chroma(lab: np.ndarray) -> np.ndarray:
    return np.hypot(lab[..., 1], lab[..., 2])


# ---------------------------------------------------------------- WCAG contrast
def relative_luminance(rgb) -> float:
    lin = srgb_to_linear(np.asarray(rgb, dtype=np.float64))
    return float(0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2])


def contrast_ratio(rgb_a, rgb_b) -> float:
    la, lb = relative_luminance(rgb_a), relative_luminance(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def wcag_report(foreground: str, background: str) -> dict:
    """Contrast ratio plus explicit pass/fail for each WCAG 2.x threshold."""
    fg, bg = normalize_hex(foreground), normalize_hex(background)
    ratio = contrast_ratio(hex_to_rgb(fg), hex_to_rgb(bg))
    checks = {
        "aa_normal_text": (4.5, "AA, normal text"),
        "aa_large_text": (3.0, "AA, large text"),
        "aaa_normal_text": (7.0, "AAA, normal text"),
        "aaa_large_text": (4.5, "AAA, large text"),
        "ui_components": (3.0, "AA, UI components and graphics"),
    }
    # Compare on the ratio rounded DOWN to 2 dp so 4.499 never shows as a pass.
    floored = int(ratio * 100) / 100
    return {
        "foreground": fg,
        "background": bg,
        "ratio": round(floored, 2),
        "checks": [
            {
                "id": key,
                "label": label,
                "threshold": threshold,
                "passed": floored >= threshold,
                "verdict": "Pass" if floored >= threshold else "Fail",
            }
            for key, (threshold, label) in checks.items()
        ],
    }


def best_text_color(background: str) -> str:
    """Black or white, whichever is more readable on ``background``."""
    rgb = hex_to_rgb(background)
    return "#000000" if contrast_ratio(rgb, (0, 0, 0)) >= contrast_ratio(rgb, (255, 255, 255)) else "#FFFFFF"


# ---------------------------------------------------------------- CVD simulation
def simulate_cvd_rgb(rgb: np.ndarray, kind: str) -> np.ndarray:
    """Simulate a colour-vision deficiency on an sRGB array (0-255, (..., 3))."""
    if kind not in CVD_MATRICES:
        raise ValueError(f"Unknown colour-vision type '{kind}'. Use one of: {', '.join(CVD_TYPES)}")
    lin = srgb_to_linear(rgb) @ CVD_MATRICES[kind].T
    return linear_to_srgb(lin)


def simulate_cvd_hex(value: str, kind: str) -> str:
    return rgb_to_hex(simulate_cvd_rgb(np.array(hex_to_rgb(value), dtype=np.float64), kind))


# ---------------------------------------------------------------- harmony
def _rotate(value: str, degrees: float, lightness: float | None = None) -> str:
    r, g, b = (c / 255 for c in hex_to_rgb(value))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + degrees / 360.0) % 1.0
    if lightness is not None:
        l = lightness
    return rgb_to_hex([c * 255 for c in colorsys.hls_to_rgb(h, l, s)])


def harmonies(value: str) -> list[dict]:
    """Classic colour-wheel harmony schemes for a base colour."""
    base = normalize_hex(value)
    r, g, b = (c / 255 for c in hex_to_rgb(base))
    _, l, _ = colorsys.rgb_to_hls(r, g, b)
    mono = sorted({min(0.92, max(0.08, l + d)) for d in (-0.3, -0.15, 0.0, 0.15, 0.3)})
    schemes = [
        ("complementary", "Complementary", "Opposite on the colour wheel; strongest hue contrast.", [0, 180]),
        ("analogous", "Analogous", "Neighbouring hues; calm and cohesive.", [-30, 0, 30]),
        ("triadic", "Triadic", "Three hues evenly spaced; balanced and lively.", [0, 120, 240]),
        ("split_complementary", "Split complementary", "Base plus the two hues beside its complement.", [0, 150, 210]),
        ("tetradic", "Tetradic", "Two complementary pairs; rich, needs one dominant colour.", [0, 90, 180, 270]),
    ]
    out = [
        {"id": sid, "name": name, "description": desc, "colors": [describe(_rotate(base, d)) for d in degs]}
        for sid, name, desc, degs in schemes
    ]
    out.append(
        {
            "id": "monochromatic",
            "name": "Monochromatic",
            "description": "One hue at several lightness levels.",
            "colors": [describe(_rotate(base, 0, lv)) for lv in mono],
        }
    )
    return out


# ---------------------------------------------------------------- naming
_HUE_NAMES = [
    (15, "red"), (45, "orange"), (70, "yellow"), (165, "green"), (200, "cyan"),
    (255, "blue"), (290, "purple"), (335, "magenta"), (360, "red"),
]


def color_name(value: str) -> str:
    """A plain-language description ("dark blue") so colour is never the only cue."""
    r, g, b = (c / 255 for c in hex_to_rgb(value))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if l < 0.06:
        return "black"
    if l > 0.95:
        return "white"
    if s < 0.12:
        return "dark gray" if l < 0.35 else "light gray" if l > 0.7 else "gray"
    deg = h * 360
    hue = next(name for limit, name in _HUE_NAMES if deg < limit)
    if hue in ("orange", "yellow") and l > 0.85:
        return "cream"
    if hue in ("orange", "yellow") and l > 0.6 and float(chroma(hex_to_lab(value))) < 30:
        return "beige"                     # pale and washed out: sand, khaki, parchment
    if hue in ("orange", "yellow") and l < 0.4:
        hue = "brown"
    elif hue == "red" and l > 0.7:
        hue = "pink"
    tone = "dark " if l < 0.3 else "light " if l > 0.72 else ""
    muted = "muted " if s < 0.35 and not tone else ""
    return f"{tone}{muted}{hue}"


def palette_names(hexes: list[str]) -> list[str]:
    """Names for a whole palette, with repeats told apart by lightness.

    Two clusters can land in the same name bucket ("red", "red"); a reader then can't
    tell which swatch a label means, so they become "red (darker)" / "red (lighter)".
    """
    names = [color_name(h) for h in hexes]
    groups: dict[str, list[int]] = {}
    for i, n in enumerate(names):
        groups.setdefault(n, []).append(i)
    for name, idx in groups.items():
        if len(idx) < 2:
            continue
        idx.sort(key=lambda i: hex_to_lab(hexes[i])[0])
        if len(idx) == 2:
            labels = ["darker", "lighter"]
        else:
            labels = ["darkest"] + [f"mid {n}" for n in range(1, len(idx) - 1)] + ["lightest"]
            if len(idx) == 3:
                labels[1] = "mid"
        for i, label in zip(idx, labels):
            names[i] = f"{name} ({label})"
    return names


def describe(value: str) -> dict:
    """Standard colour object used across the API."""
    h = normalize_hex(value)
    return {
        "hex": h,
        "rgb": list(hex_to_rgb(h)),
        "name": color_name(h),
        "text_color": best_text_color(h),
    }
