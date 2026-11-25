"""Unit tests for colour science, ETL, clustering and the ranking model."""
import io

import numpy as np
import pytest
from PIL import Image

from paletteai import color as C
from paletteai import ranking
from paletteai.clustering import FALLBACK_K, extract_clusters
from paletteai.etl import IngestError, normalise_image

from conftest import blocks_image, png_bytes


# ------------------------------------------------------------------ colour
def test_hex_normalisation():
    assert C.normalize_hex("abc") == "#AABBCC"
    assert C.normalize_hex(" #1a2B3c ") == "#1A2B3C"
    with pytest.raises(ValueError):
        C.normalize_hex("#12345")


def test_contrast_extremes_and_known_value():
    assert C.contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert C.contrast_ratio((10, 20, 30), (10, 20, 30)) == pytest.approx(1.0)
    # #767676 on white is the classic "just passes AA" grey (4.54:1)
    report = C.wcag_report("#767676", "#FFFFFF")
    assert report["ratio"] == 4.54
    verdicts = {c["id"]: c["passed"] for c in report["checks"]}
    assert verdicts["aa_normal_text"] and not verdicts["aaa_normal_text"]


def test_lab_reference_values_and_roundtrip():
    assert C.rgb_to_lab(np.array([255.0, 255, 255])) == pytest.approx([100, 0, 0], abs=0.01)
    assert C.rgb_to_lab(np.array([255.0, 0, 0])) == pytest.approx([53.24, 80.09, 67.20], abs=0.05)
    rgb = np.array([[12.0, 200, 99], [250, 128, 3], [0, 0, 0], [77, 77, 200]])
    assert C.lab_to_rgb(C.rgb_to_lab(rgb)) == pytest.approx(rgb, abs=1)


def test_cvd_simulation_properties():
    # Greys are unchanged; red and green collapse towards each other for deuteranopia.
    assert C.simulate_cvd_hex("#808080", "protanopia") == "#808080"
    red, green = C.hex_to_lab("#E34948"), C.hex_to_lab("#5A9E3A")
    sim_r = C.hex_to_lab(C.simulate_cvd_hex("#E34948", "deuteranopia"))
    sim_g = C.hex_to_lab(C.simulate_cvd_hex("#5A9E3A", "deuteranopia"))
    assert np.linalg.norm(sim_r - sim_g) < np.linalg.norm(red - green)
    with pytest.raises(ValueError):
        C.simulate_cvd_hex("#000000", "unknown")


def test_harmonies_and_names():
    schemes = {s["id"]: s for s in C.harmonies("#FF0000")}
    assert schemes["complementary"]["colors"][1]["hex"] == "#00FFFF"
    assert [c["hex"] for c in schemes["triadic"]["colors"]] == ["#FF0000", "#00FF00", "#0000FF"]
    assert C.color_name("#000000") == "black"
    assert C.color_name("#0A1E78") == "dark blue"
    assert C.color_name("#808080") == "gray"
    assert C.color_name("#DDCFA8") == "beige"
    assert C.palette_names(["#E36D56", "#12558B", "#C14E61"]) == ["red (lighter)", "blue", "red (darker)"]


# ------------------------------------------------------------------ ETL
def test_etl_rejects_bad_input():
    for data, code in [(b"", "empty_file"), (b"this is not an image", "invalid_image")]:
        with pytest.raises(IngestError) as err:
            normalise_image(data)
        assert err.value.code == code
    truncated = blocks_image([(255, 0, 0), (0, 0, 255)])[:60]
    with pytest.raises(IngestError):
        normalise_image(truncated)


def test_etl_rejects_unsupported_format():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, "TIFF")
    with pytest.raises(IngestError) as err:
        normalise_image(buf.getvalue())
    assert err.value.code == "unsupported_type" and err.value.status == 415


def test_etl_handles_transparency():
    im = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    im.paste((200, 30, 30, 255), (0, 0, 20, 40))
    norm = normalise_image(png_bytes(im))
    assert norm.has_alpha and norm.transparent_fraction == pytest.approx(0.5)
    assert {tuple(p) for p in norm.pixels} == {(200, 30, 30)}       # transparent pixels ignored
    with pytest.raises(IngestError) as err:
        normalise_image(png_bytes(Image.new("RGBA", (10, 10), (0, 0, 0, 0))))
    assert err.value.code == "fully_transparent"


def test_etl_resizes_large_images_and_is_deterministic():
    rng = np.random.default_rng(1)
    data = png_bytes(Image.fromarray(rng.integers(0, 255, (900, 1200, 3), dtype=np.uint8)))
    a, b = normalise_image(data), normalise_image(data)
    assert (a.width, a.height) == (1200, 900)
    assert len(a.pixels) == 5000 and np.array_equal(a.pixels, b.pixels)
    assert max(Image.open(io.BytesIO(a.preview_jpeg)).size) == 640


# ------------------------------------------------------------------ clustering
def _blobs(centers, n=300, spread=2.0, seed=0):
    rng = np.random.default_rng(seed)
    return np.vstack([rng.normal(c, spread, (n, 3)) for c in centers])


def test_auto_k_recovers_true_cluster_count():
    centers = [(20, 0, 0), (50, 60, 40), (80, -50, 30), (60, 10, -60)]
    result = extract_clusters(_blobs(centers))
    assert result.k == 4 and result.method == "auto" and not result.fallback_used
    assert result.silhouette > 0.8
    assert set(result.k_scores) == {3, 4, 5, 6, 7, 8}
    assert result.shares.sum() == pytest.approx(1.0)
    for c in centers:   # every true centre is recovered closely
        assert np.min(np.linalg.norm(result.centers - np.array(c), axis=1)) < 1.5


def test_auto_k_falls_back_when_inconclusive():
    noise = np.random.default_rng(3).uniform([0, -60, -60], [100, 60, 60], (1500, 3))
    result = extract_clusters(noise)
    assert result.fallback_used and result.k == FALLBACK_K
    assert "inconclusive" in result.reason


def test_manual_k_and_shares_sorted():
    X = np.vstack([_blobs([(30, 0, 0)], n=600), _blobs([(70, 40, 40)], n=200)])
    result = extract_clusters(X, requested_k=2)
    assert result.k == 2 and result.method == "manual"
    assert result.shares == pytest.approx([0.75, 0.25])


def test_few_distinct_colours_used_directly():
    X = np.array([[50.0, 10, 10]] * 30 + [[80.0, -20, 5]] * 10)
    result = extract_clusters(X)
    assert result.k == 2 and result.shares == pytest.approx([0.75, 0.25])
    assert extract_clusters(X, requested_k=7).k == 2


def test_clustering_is_deterministic():
    X = _blobs([(20, 0, 0), (50, 60, 40), (80, -50, 30)])
    a, b = extract_clusters(X, seed=7), extract_clusters(X, seed=7)
    assert np.array_equal(a.centers, b.centers)


# ------------------------------------------------------------------ ranking
def _pal(*hexes):
    return ranking.palette_features([{"hex": h, "share": 1 / len(hexes)} for h in hexes])


PASTEL_A = _pal("#F6D6D6", "#D6E6F6", "#E6F6D6", "#F6F0D6")
PASTEL_B = _pal("#F2DCE8", "#DCEBF2", "#E8F2DC")
DARK_A = _pal("#101820", "#1E2A38", "#2A1E38", "#0C0C10")
DARK_B = _pal("#141414", "#22303C", "#30223C")
VIVID = _pal("#FF0033", "#00CC44", "#0055FF", "#FFCC00")


def test_features_are_bounded_and_meaningful():
    for feats in (PASTEL_A, DARK_A, VIVID):
        assert set(feats) == set(ranking.FEATURE_KEYS)
        assert all(0 <= v <= 1 for v in feats.values())
    assert PASTEL_A["mean_lightness"] > 0.8 > 0.2 > DARK_A["mean_lightness"]
    assert VIVID["mean_chroma"] > PASTEL_A["mean_chroma"]
    assert VIVID["hue_diversity"] > _pal("#AA2020", "#CC3030", "#EE5050")["hue_diversity"]


def test_cold_start_scores_zero():
    model = ranking.fit([], [])
    assert model["status"] == "cold_start"
    assert ranking.score(model, VIVID)["score"] == 0


def test_ranking_follows_feedback():
    model = ranking.fit(liked=[PASTEL_A], disliked=[DARK_A])
    s_pastel, s_dark, s_vivid = (ranking.score(model, f)["score"] for f in (PASTEL_B, DARK_B, VIVID))
    assert s_pastel > s_vivid > s_dark
    assert s_pastel > 0.5 and s_dark < -0.5
    # Flipping the feedback flips the order: the model is driven by data, not fixed taste.
    flipped = ranking.fit(liked=[DARK_A], disliked=[PASTEL_A])
    assert ranking.score(flipped, DARK_B)["score"] > ranking.score(flipped, PASTEL_B)["score"]


def test_feature_weights_adapt_to_feedback():
    model = ranking.fit(liked=[PASTEL_A], disliked=[DARK_A])
    w = model["feature_weights"]
    assert w["mean_lightness"] == max(w.values())          # the feature that separates the two sets
    assert np.mean(list(w.values())) == pytest.approx(1.0, abs=0.01)
    assert ranking.score(model, PASTEL_B)["reasons"]
