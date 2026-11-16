"""End-to-end API tests (each test gets a fresh database in a temp folder)."""
import pytest
from PIL import Image

from conftest import blocks_image, png_bytes, upload

RED, NAVY, CREAM, TEAL = (226, 86, 60), (28, 52, 84), (245, 239, 224), (44, 142, 132)


def test_upload_extracts_exact_flat_colours(client):
    r = upload(client, blocks_image([RED, NAVY, CREAM, TEAL]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cached"] is False
    p = body["palette"]
    assert {c["hex"] for c in p["colors"]} == {"#E2563C", "#1C3454", "#F5EFE0", "#2C8E84"}
    assert sum(c["share"] for c in p["colors"]) == pytest.approx(1.0, abs=0.01)
    assert all(c["percent"] == pytest.approx(25.0, abs=2) for c in p["colors"])   # 5000-pixel sample
    assert p["method"] == "auto" and p["k"] == 4
    assert all(set(c["cvd"]) == {"protanopia", "deuteranopia", "tritanopia"} and c["name"] for c in p["colors"])
    assert p["accessibility"]["total_pairs"] == 6
    best = p["accessibility"]["best_pair"]
    assert {best["a_hex"], best["b_hex"]} == {"#1C3454", "#F5EFE0"} and best["aa_normal_text"]


def test_identical_upload_is_deduplicated(client):
    data = blocks_image([RED, NAVY, CREAM])
    first, second = upload(client, data).json(), upload(client, data, name="renamed.png").json()
    assert second["cached"] is True and second["palette"]["id"] == first["palette"]["id"]
    manual = upload(client, data, k=2).json()                       # different setting -> new palette, same image
    assert manual["cached"] is False and manual["palette"]["k"] == 2
    assert manual["palette"]["image"]["id"] == first["palette"]["image"]["id"]
    assert client.get("/api/v1/health").json()["palettes"] == 2


@pytest.mark.parametrize("data,status,code", [
    (b"", 400, "empty_file"),
    (b"definitely not an image", 415, "invalid_image"),
    (b"x" * (10 * 1024 * 1024 + 1), 413, "file_too_large"),
])
def test_invalid_uploads_get_clear_errors_and_store_nothing(client, data, status, code):
    r = upload(client, data)
    assert r.status_code == status
    assert r.json()["detail"]["code"] == code and r.json()["detail"]["message"]
    assert client.get("/api/v1/health").json()["palettes"] == 0
    assert client.get("/api/v1/palettes").json() == []


def test_k_out_of_range_rejected(client):
    r = upload(client, blocks_image([RED, NAVY]), k=50)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_error"


def test_samples_and_previews(client):
    samples = client.get("/api/v1/samples").json()
    assert len(samples) == 6
    for s in samples:
        r = client.post(f"/api/v1/samples/{s['id']}/extract")
        assert r.status_code == 200, (s, r.text)
        p = r.json()["palette"]
        assert 2 <= p["k"] <= 8 and p["image"]["source"] == "sample"
    logo = client.post("/api/v1/samples/logo/extract").json()
    assert logo["cached"] is True and logo["palette"]["image"]["has_alpha"]
    assert logo["palette"]["k"] == 3                               # transparent background is not a colour
    image_id = logo["palette"]["image"]["id"]
    plain = client.get(f"/api/v1/images/{image_id}/preview")
    sim = client.get(f"/api/v1/images/{image_id}/preview", params={"cvd": "protanopia"})
    assert plain.headers["content-type"] == sim.headers["content-type"] == "image/jpeg"
    assert plain.content != sim.content
    assert client.get("/api/v1/images/999/preview").status_code == 404
    assert client.post("/api/v1/samples/nope/extract").status_code == 404


def test_library_is_per_user(client):
    pid = upload(client, blocks_image([RED, NAVY, CREAM])).json()["palette"]["id"]
    r = client.put(f"/api/v1/palettes/{pid}/save", json={"user": "asha", "name": "Poster"})
    assert r.json()["saved"] and r.json()["saved_name"] == "Poster"
    assert [p["id"] for p in client.get("/api/v1/palettes", params={"user": "asha", "saved": True}).json()] == [pid]
    assert client.get("/api/v1/palettes", params={"user": "ravi", "saved": True}).json() == []
    assert client.delete(f"/api/v1/palettes/{pid}/save", params={"user": "asha"}).json()["saved"] is False
    assert client.put("/api/v1/palettes/999/save", json={"user": "asha"}).status_code == 404
    assert client.get("/api/v1/palettes", params={"user": "bad user!"}).status_code == 422


def test_feedback_changes_ranking(client):
    def make(colors, name):
        pid = upload(client, blocks_image(colors), name=name).json()["palette"]["id"]
        client.put(f"/api/v1/palettes/{pid}/save", json={"user": "asha"})
        return pid

    pastel_a = make([(246, 214, 214), (214, 230, 246), (230, 246, 214)], "pastel_a.png")
    dark_a = make([(16, 24, 32), (30, 42, 56), (42, 30, 56)], "dark_a.png")
    pastel_b = make([(242, 220, 232), (220, 235, 242), (232, 242, 220)], "pastel_b.png")
    dark_b = make([(20, 20, 20), (34, 48, 60), (48, 34, 60)], "dark_b.png")

    cold = client.get("/api/v1/ranking", params={"user": "asha"}).json()
    assert cold["model"]["status"] == "cold_start" and all(i["score"] == 0 for i in cold["items"])
    assert [i["palette"]["id"] for i in cold["items"]] == [dark_b, pastel_b, dark_a, pastel_a]   # newest first

    client.put(f"/api/v1/palettes/{pastel_a}/feedback", json={"user": "asha", "value": "like"})
    client.put(f"/api/v1/palettes/{dark_a}/feedback", json={"user": "asha", "value": "dislike"})
    ranked = client.get("/api/v1/ranking", params={"user": "asha"}).json()
    assert ranked["model"] | {"liked": 1, "disliked": 1, "status": "personalised"} == ranked["model"]
    assert [i["palette"]["id"] for i in ranked["items"]] == [pastel_a, pastel_b, dark_b, dark_a]
    assert [i["rank"] for i in ranked["items"]] == [1, 2, 3, 4]
    assert ranked["items"][0]["palette"]["feedback"] == "like" and ranked["items"][0]["reasons"]

    # Reversing the feedback reverses the ranking; another user is unaffected.
    client.put(f"/api/v1/palettes/{pastel_a}/feedback", json={"user": "asha", "value": "dislike"})
    client.put(f"/api/v1/palettes/{dark_a}/feedback", json={"user": "asha", "value": "like"})
    flipped = client.get("/api/v1/ranking", params={"user": "asha"}).json()
    assert [i["palette"]["id"] for i in flipped["items"]] == [dark_a, dark_b, pastel_b, pastel_a]
    assert client.get("/api/v1/ranking", params={"user": "ravi", "scope": "all"}).json()["model"]["status"] == "cold_start"

    client.delete(f"/api/v1/palettes/{pastel_a}/feedback", params={"user": "asha"})
    client.delete(f"/api/v1/palettes/{dark_a}/feedback", params={"user": "asha"})
    assert client.get("/api/v1/ranking", params={"user": "asha"}).json()["model"]["status"] == "cold_start"


def test_colour_tools(client):
    r = client.post("/api/v1/contrast", json={"foreground": "#000", "background": "#FFFFFF"}).json()
    assert r["ratio"] == 21.0 and all(c["verdict"] == "Pass" for c in r["checks"])
    r = client.post("/api/v1/contrast", json={"foreground": "#777777", "background": "#FFFFFF"}).json()
    assert {c["id"]: c["verdict"] for c in r["checks"]}["aa_normal_text"] == "Fail"       # 4.47:1
    bad = client.post("/api/v1/contrast", json={"foreground": "red", "background": "#FFF"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_color"

    h = client.post("/api/v1/harmony", json={"color": "#2A78D6"}).json()
    assert h["base"]["hex"] == "#2A78D6"
    assert [s["id"] for s in h["schemes"]] == ["complementary", "analogous", "triadic", "split_complementary", "tetradic", "monochromatic"]

    c = client.post("/api/v1/cvd/simulate", json={"colors": ["#E34948", "#008300"], "types": ["deuteranopia"]}).json()
    assert c["types"] == ["deuteranopia"] and len(c["colors"]) == 2
    assert c["colors"][0]["simulated"]["deuteranopia"]["hex"] != "#E34948"


def test_local_plugin_panel_and_routes(client):
    plugins = client.get("/api/v1/plugins").json()
    tints = next(p for p in plugins if p["name"] == "tints_and_shades")
    assert tints["kind"] == "local" and tints["slots"] == ["palette.result"] and tints["error"] is None
    pid = upload(client, blocks_image([RED, NAVY, CREAM])).json()["palette"]["id"]
    panels = client.get("/api/v1/panels", params={"slot": "palette.result", "palette_id": pid}).json()
    assert panels[0]["ok"] and panels[0]["panel"]["title"] == "Tints & shades"
    scales = client.get(f"{tints['routes_prefix']}/palettes/{pid}").json()
    assert len(scales["scales"]) == 3 and len(scales["scales"][0]["steps"]) == 5
    assert "/api/v1/ext/tints_and_shades/palettes/{palette_id}" in client.get("/openapi.json").json()["paths"]
    assert client.get("/api/v1/panels", params={"slot": "palette.result"}).status_code == 422


def test_webhook_plugin_lifecycle_and_failure_isolation(client, monkeypatch):
    import httpx
    from paletteai.plugins import PluginManager

    reg = {"name": "Remote demo", "hook_url": "http://127.0.0.1:9/hook", "slots": ["palette.result"]}
    created = client.post("/api/v1/plugins", json=reg)
    assert created.status_code == 201 and created.json()["kind"] == "webhook"
    assert client.post("/api/v1/plugins", json=reg).status_code == 409
    assert client.post("/api/v1/plugins", json={**reg, "name": "x2", "hook_url": "not a url"}).status_code == 422
    pid = upload(client, blocks_image([RED, NAVY, CREAM])).json()["palette"]["id"]

    seen = {}

    def good(url, context):
        seen.update(url=url, context=context)
        return {"title": "Remote", "blocks": [{"type": "stat", "label": "Colours", "value": str(context["palette"]["k"])}]}

    monkeypatch.setattr(PluginManager, "_call_hook", staticmethod(good))
    remote = client.get("/api/v1/panels", params={"palette_id": pid}).json()[-1]
    assert remote["ok"] and remote["panel"]["blocks"][0]["value"] == "3"
    assert seen["url"] == reg["hook_url"] and seen["context"]["api_base"].endswith("/api/v1")

    # invalid panel, timeout: reported per plugin, the other plugin still renders
    monkeypatch.setattr(PluginManager, "_call_hook", staticmethod(lambda u, c: {"title": "x", "blocks": [{"type": "script"}]}))
    results = client.get("/api/v1/panels", params={"palette_id": pid}).json()
    assert results[0]["ok"] and not results[-1]["ok"] and "invalid panel" in results[-1]["error"]

    def slow(url, context):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(PluginManager, "_call_hook", staticmethod(slow))
    assert "did not answer" in client.get("/api/v1/panels", params={"palette_id": pid}).json()[-1]["error"]

    assert client.delete(f"/api/v1/plugins/{created.json()['id']}").status_code == 204
    assert client.delete(f"/api/v1/plugins/{created.json()['id']}").status_code == 404
    assert len(client.get("/api/v1/panels", params={"palette_id": pid}).json()) == 1


def test_openapi_and_frontend_served(client):
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "PaletteAI"
    for path in ("/api/v1/images", "/api/v1/palettes/{palette_id}", "/api/v1/contrast", "/api/v1/harmony",
                 "/api/v1/cvd/simulate", "/api/v1/palettes/{palette_id}/feedback", "/api/v1/ranking", "/api/v1/plugins"):
        assert path in spec["paths"], path
    assert {"Panel", "PanelRequest", "Palette"} <= set(spec["components"]["schemas"])
    assert client.get("/docs").status_code == 200
    home = client.get("/")
    assert home.status_code == 200 and "PaletteAI" in home.text


def test_gif_and_jpeg_accepted(client):
    import io
    for fmt in ("GIF", "JPEG", "WEBP", "BMP"):
        buf = io.BytesIO()
        Image.new("RGB", (30, 30), (200, 40, 40)).save(buf, fmt)
        r = client.post("/api/v1/images", files={"file": (f"a.{fmt.lower()}", buf.getvalue(), "application/octet-stream")})
        assert r.status_code == 200, (fmt, r.text)
        assert r.json()["palette"]["k"] == 1
