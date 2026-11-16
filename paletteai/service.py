"""Business logic shared by the REST API and in-process plugins."""
from __future__ import annotations

import json
import sqlite3

import numpy as np

from . import color as C
from . import ranking
from .clustering import extract_clusters
from .config import API_PREFIX, Settings
from .etl import IngestError, content_hash, normalise_image


class NotFound(Exception):
    pass


# ---------------------------------------------------------------- extraction
def _build_colors(result) -> list[dict]:
    merged: dict[str, dict] = {}
    rgbs = C.lab_to_rgb(result.centers)
    for rgb, share in zip(rgbs, result.shares):
        hex_ = C.rgb_to_hex(rgb)
        if hex_ in merged:
            merged[hex_]["share"] += float(share)
            continue
        lab = C.hex_to_lab(hex_)
        merged[hex_] = {
            "hex": hex_,
            "rgb": [int(v) for v in rgb],
            "lab": [round(float(v), 2) for v in lab],
            "share": float(share),
            "name": C.color_name(hex_),
        }
    colors = sorted(merged.values(), key=lambda c: -c["share"])
    for c in colors:
        c["share"] = round(c["share"], 4)
    return colors


def accessibility_summary(colors: list[dict]) -> dict:
    """WCAG contrast for every colour pair plus each colour on white / black."""
    pairs = []
    for i in range(len(colors)):
        for j in range(i + 1, len(colors)):
            ratio = int(C.contrast_ratio(colors[i]["rgb"], colors[j]["rgb"]) * 100) / 100
            pairs.append({
                "a": i, "b": j,
                "a_hex": colors[i]["hex"], "b_hex": colors[j]["hex"],
                "ratio": ratio,
                "aa_normal_text": ratio >= 4.5,
                "aa_large_text": ratio >= 3.0,
                "aaa_normal_text": ratio >= 7.0,
            })
    pairs.sort(key=lambda p: -p["ratio"])
    return {
        "pairs": pairs,
        "total_pairs": len(pairs),
        "aa_normal_pairs": sum(p["aa_normal_text"] for p in pairs),
        "aa_large_pairs": sum(p["aa_large_text"] for p in pairs),
        "best_pair": pairs[0] if pairs else None,
        "on_white": [int(C.contrast_ratio(c["rgb"], (255, 255, 255)) * 100) / 100 for c in colors],
        "on_black": [int(C.contrast_ratio(c["rgb"], (0, 0, 0)) * 100) / 100 for c in colors],
    }


def extract_palette(settings: Settings, conn: sqlite3.Connection, data: bytes, filename: str,
                    source: str = "upload", requested_k: int | None = None) -> tuple[int, bool]:
    """Run the full ETL + clustering pipeline. Returns ``(palette_id, cached)``.

    An identical file (same SHA-256) with the same colour-count setting is
    served from the database instead of being processed again.
    """
    k_key = requested_k or 0
    digest = content_hash(data) if data else ""
    row = conn.execute(
        "SELECT p.id FROM palettes p JOIN images i ON i.id = p.image_id WHERE i.content_hash = ? AND p.requested_k = ?",
        (digest, k_key),
    ).fetchone()
    if row:
        return row["id"], True

    norm = normalise_image(data, filename)          # raises IngestError on bad input
    try:
        lab = C.rgb_to_lab(norm.pixels.astype(np.float64))
        result = extract_clusters(lab, requested_k, seed=int(norm.content_hash[:8], 16))
        colors = _build_colors(result)
        features = ranking.palette_features(colors)
        access = accessibility_summary(colors)
    except Exception as exc:  # a failed job must not leave partial rows behind
        raise IngestError("processing_failed", f"Palette extraction failed: {exc}", 500) from exc

    with conn:  # one transaction: image + palette, or nothing
        image = conn.execute("SELECT id FROM images WHERE content_hash = ?", (norm.content_hash,)).fetchone()
        if image:
            image_id = image["id"]
        else:
            image_id = conn.execute(
                "INSERT INTO images (content_hash, filename, mime, width, height, size_bytes, has_alpha,"
                " transparent_fraction, source) VALUES (?,?,?,?,?,?,?,?,?)",
                (norm.content_hash, norm.filename, norm.mime, norm.width, norm.height, norm.size_bytes,
                 int(norm.has_alpha), norm.transparent_fraction, source),
            ).lastrowid
            settings.previews_dir.mkdir(parents=True, exist_ok=True)
            (settings.previews_dir / f"{norm.content_hash}.jpg").write_bytes(norm.preview_jpeg)
        palette_id = conn.execute(
            "INSERT INTO palettes (image_id, requested_k, k, method, silhouette, k_scores_json, fallback_used,"
            " reason, colors_json, features_json, accessibility_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (image_id, k_key, len(colors), result.method, result.silhouette, json.dumps(result.k_scores),
             int(result.fallback_used), result.reason, json.dumps(colors), json.dumps(features), json.dumps(access)),
        ).lastrowid
    return palette_id, False


# ---------------------------------------------------------------- reading
_PALETTE_SELECT = """
SELECT p.*, i.filename, i.width, i.height, i.source, i.has_alpha, i.transparent_fraction,
       i.content_hash, i.mime, i.size_bytes,
       l.name AS saved_name, l.saved_at, f.value AS feedback_value
FROM palettes p
JOIN images i ON i.id = p.image_id
LEFT JOIN library l ON l.palette_id = p.id AND l.user_id = :user
LEFT JOIN feedback f ON f.palette_id = p.id AND f.user_id = :user
"""


def _palette_dict(row: sqlite3.Row) -> dict:
    colors = json.loads(row["colors_json"])
    for c in colors:
        c["percent"] = round(c["share"] * 100, 1)
        c["text_color"] = C.best_text_color(c["hex"])
        c["cvd"] = {kind: C.simulate_cvd_hex(c["hex"], kind) for kind in C.CVD_TYPES}
    scores = json.loads(row["k_scores_json"])
    return {
        "id": row["id"],
        "image": {
            "id": row["image_id"],
            "filename": row["filename"],
            "mime": row["mime"],
            "width": row["width"],
            "height": row["height"],
            "size_bytes": row["size_bytes"],
            "source": row["source"],
            "has_alpha": bool(row["has_alpha"]),
            "transparent_fraction": row["transparent_fraction"],
            "content_hash": row["content_hash"],
            "preview_url": f"{API_PREFIX}/images/{row['image_id']}/preview",
        },
        "k": row["k"],
        "method": row["method"],
        "requested_k": row["requested_k"] or None,
        "silhouette": row["silhouette"],
        "k_scores": [{"k": int(k), "silhouette": s, "chosen": int(k) == row["k"]} for k, s in sorted(scores.items(), key=lambda kv: int(kv[0]))],
        "fallback_used": bool(row["fallback_used"]),
        "reason": row["reason"],
        "colors": colors,
        "features": json.loads(row["features_json"]),
        "accessibility": json.loads(row["accessibility_json"]),
        "saved": row["saved_name"] is not None,
        "saved_name": row["saved_name"],
        "feedback": {1: "like", -1: "dislike"}.get(row["feedback_value"]),
        "created_at": row["created_at"],
    }


def get_palette(conn: sqlite3.Connection, palette_id: int, user: str = "guest") -> dict:
    row = conn.execute(_PALETTE_SELECT + " WHERE p.id = :id", {"user": user, "id": palette_id}).fetchone()
    if not row:
        raise NotFound(f"Palette {palette_id} does not exist.")
    return _palette_dict(row)


def list_palettes(conn: sqlite3.Connection, user: str = "guest", saved_only: bool = False, limit: int = 100) -> list[dict]:
    where = " WHERE l.name IS NOT NULL" if saved_only else ""
    order = " ORDER BY COALESCE(l.saved_at, p.created_at) DESC, p.id DESC LIMIT :limit"
    rows = conn.execute(_PALETTE_SELECT + where + order, {"user": user, "limit": limit}).fetchall()
    return [_palette_dict(r) for r in rows]


def _require_palette(conn: sqlite3.Connection, palette_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT p.id, i.filename FROM palettes p JOIN images i ON i.id = p.image_id WHERE p.id = ?", (palette_id,)
    ).fetchone()
    if not row:
        raise NotFound(f"Palette {palette_id} does not exist.")
    return row


# ---------------------------------------------------------------- library & feedback
def save_palette(conn: sqlite3.Connection, palette_id: int, user: str, name: str | None = None) -> None:
    row = _require_palette(conn, palette_id)
    default = row["filename"].rsplit(".", 1)[0] or f"Palette {palette_id}"
    with conn:
        conn.execute(
            "INSERT INTO library (user_id, palette_id, name) VALUES (?,?,?)"
            " ON CONFLICT (user_id, palette_id) DO UPDATE SET name = excluded.name",
            (user, palette_id, (name or default).strip()[:80] or default),
        )


def unsave_palette(conn: sqlite3.Connection, palette_id: int, user: str) -> None:
    _require_palette(conn, palette_id)
    with conn:
        conn.execute("DELETE FROM library WHERE user_id = ? AND palette_id = ?", (user, palette_id))


def set_feedback(conn: sqlite3.Connection, palette_id: int, user: str, value: str | None) -> None:
    _require_palette(conn, palette_id)
    with conn:
        if value is None:
            conn.execute("DELETE FROM feedback WHERE user_id = ? AND palette_id = ?", (user, palette_id))
        else:
            conn.execute(
                "INSERT INTO feedback (user_id, palette_id, value) VALUES (?,?,?)"
                " ON CONFLICT (user_id, palette_id) DO UPDATE SET value = excluded.value,"
                " updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
                (user, palette_id, 1 if value == "like" else -1),
            )


# ---------------------------------------------------------------- ranking
def rank_palettes(conn: sqlite3.Connection, user: str, scope: str = "saved") -> dict:
    """Rank palettes for ``user`` with the feedback-trained preference model."""
    rated = conn.execute(
        "SELECT f.value, p.features_json FROM feedback f JOIN palettes p ON p.id = f.palette_id WHERE f.user_id = ?",
        (user,),
    ).fetchall()
    model = ranking.fit(
        [json.loads(r["features_json"]) for r in rated if r["value"] == 1],
        [json.loads(r["features_json"]) for r in rated if r["value"] == -1],
    )
    palettes = list_palettes(conn, user, saved_only=(scope == "saved"), limit=500)
    items = [{"palette": p, **ranking.score(model, p["features"])} for p in palettes]
    if model["status"] == "personalised":
        items.sort(key=lambda it: -it["score"])     # stable: ties keep newest-first
    for n, it in enumerate(items, start=1):
        it["rank"] = n
    return {"user": user, "scope": scope, "model": ranking.public_model(model), "items": items}
