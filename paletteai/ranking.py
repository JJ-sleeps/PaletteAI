"""Preference ranking: learn from a user's like/dislike feedback.

Each palette is described by a small feature vector. A palette's score is its
(kernel) similarity to the palettes the user liked minus its similarity to the
ones they disliked. Feature weights adapt too: features on which the liked and
disliked sets differ most count for more.
"""
from __future__ import annotations

import math

import numpy as np

from . import color as C

FEATURES = [
    ("mean_lightness", "overall lightness"),
    ("lightness_spread", "light/dark variety"),
    ("mean_chroma", "overall colourfulness"),
    ("max_chroma", "most vivid colour"),
    ("contrast_spread", "strongest internal contrast"),
    ("size", "number of colours"),
    ("warmth", "warm/cool balance"),
    ("hue_diversity", "hue variety"),
]
FEATURE_KEYS = [k for k, _ in FEATURES]
FEATURE_LABELS = dict(FEATURES)
KERNEL_WIDTH = 0.35


def palette_features(colors: list[dict]) -> dict[str, float]:
    """Feature vector for a palette; every feature is scaled to roughly 0-1.

    ``colors`` items need ``hex`` and ``share``.
    """
    rgbs = np.array([C.hex_to_rgb(c["hex"]) for c in colors], dtype=np.float64)
    w = np.array([max(float(c.get("share", 0)), 1e-6) for c in colors])
    w = w / w.sum()
    lab = C.rgb_to_lab(rgbs)
    L, chroma = lab[:, 0], C.chroma(lab)
    hue = np.arctan2(lab[:, 2], lab[:, 1])
    mean_L = float((w * L).sum())
    cw = w * chroma
    cw_total = float(cw.sum())
    max_contrast = 1.0
    for i in range(len(rgbs)):
        for j in range(i + 1, len(rgbs)):
            max_contrast = max(max_contrast, C.contrast_ratio(rgbs[i], rgbs[j]))
    if cw_total > 1e-6:
        # Lab hue ~70 deg sits between red and yellow: the "warm" direction.
        warmth = float((cw * np.cos(hue - math.radians(70))).sum() / cw_total)
        resultant = math.hypot(float((cw * np.cos(hue)).sum()), float((cw * np.sin(hue)).sum())) / cw_total
    else:
        warmth, resultant = 0.0, 1.0
    # Hue is unreliable for near-grey palettes, so fade the hue features out with chroma.
    hue_confidence = min(1.0, cw_total / 20)
    warmth *= hue_confidence
    resultant = 1 - (1 - resultant) * hue_confidence
    feats = {
        "mean_lightness": mean_L / 100,
        "lightness_spread": min(1.0, math.sqrt(float((w * (L - mean_L) ** 2).sum())) / 40),
        "mean_chroma": min(1.0, float(cw.sum()) / 80),
        "max_chroma": min(1.0, float(chroma.max()) / 120),
        "contrast_spread": math.log(max_contrast) / math.log(21),
        "size": min(1.0, len(colors) / 10),
        "warmth": (warmth + 1) / 2,
        "hue_diversity": 1 - resultant,
    }
    return {k: round(v, 4) for k, v in feats.items()}


def _vec(features: dict[str, float]) -> np.ndarray:
    return np.array([features.get(k, 0.0) for k in FEATURE_KEYS], dtype=np.float64)


def fit(liked: list[dict], disliked: list[dict]) -> dict:
    """Build the per-user preference model from liked / disliked feature dicts."""
    A = np.array([_vec(f) for f in liked]) if liked else np.empty((0, len(FEATURE_KEYS)))
    B = np.array([_vec(f) for f in disliked]) if disliked else np.empty((0, len(FEATURE_KEYS)))
    weights = np.ones(len(FEATURE_KEYS))
    if len(A) and len(B):
        weights = 1 + 4 * np.abs(A.mean(0) - B.mean(0))
        weights = weights / weights.mean()
    return {
        "status": "personalised" if (len(A) or len(B)) else "cold_start",
        "liked": len(A),
        "disliked": len(B),
        "feature_weights": {k: round(float(x), 3) for k, x in zip(FEATURE_KEYS, weights)},
        "liked_centroid": {k: round(float(x), 3) for k, x in zip(FEATURE_KEYS, A.mean(0))} if len(A) else None,
        "disliked_centroid": {k: round(float(x), 3) for k, x in zip(FEATURE_KEYS, B.mean(0))} if len(B) else None,
        "_A": A, "_B": B, "_w": weights,
    }


def _similarity(x: np.ndarray, refs: np.ndarray, w: np.ndarray) -> float:
    if not len(refs):
        return 0.0
    d2 = (w * (refs - x) ** 2).sum(1)
    return float(np.exp(-d2 / (2 * KERNEL_WIDTH**2)).mean())


def score(model: dict, features: dict[str, float]) -> dict:
    """Score one palette in [-1, 1] with a plain-language explanation."""
    x = _vec(features)
    like = _similarity(x, model["_A"], model["_w"])
    dislike = _similarity(x, model["_B"], model["_w"])
    reasons: list[str] = []
    if model["status"] == "cold_start":
        reasons.append("No feedback yet: like or dislike a few palettes to personalise this ranking.")
    else:
        w = model["_w"]
        if len(model["_A"]):
            gap = w * np.abs(model["_A"].mean(0) - x)
            near = [FEATURE_LABELS[FEATURE_KEYS[i]] for i in np.argsort(gap)[:2] if gap[i] < 0.12]
            if near and like >= 0.5:
                reasons.append("Close to palettes you liked in " + " and ".join(near) + ".")
            elif like < 0.3:
                far = FEATURE_LABELS[FEATURE_KEYS[int(np.argmax(gap))]]
                reasons.append(f"Differs from palettes you liked, mainly in {far}.")
        if len(model["_B"]) and dislike >= 0.5:
            gap = w * np.abs(model["_B"].mean(0) - x)
            near = FEATURE_LABELS[FEATURE_KEYS[int(np.argmin(gap))]]
            reasons.append(f"Resembles palettes you disliked in {near}.")
        if not reasons:
            reasons.append("Moderately similar to your rated palettes.")
    return {
        "score": round(like - dislike, 4),
        "like_similarity": round(like, 4),
        "dislike_similarity": round(dislike, 4),
        "reasons": reasons,
    }


def public_model(model: dict) -> dict:
    out = {k: v for k, v in model.items() if not k.startswith("_")}
    out["feature_labels"] = FEATURE_LABELS
    return out
