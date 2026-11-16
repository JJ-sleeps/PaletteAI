"""Tests for the cross-dev plugin (Palette Fixer & Re-Scorer)."""
import httpx
import pytest
from fastapi.testclient import TestClient

from crossdev.palette_fixer import app as plugin
from crossdev.palette_fixer import fixer, mock_vibecheck
from crossdev.palette_fixer.fixer import ciede2000


@pytest.fixture()
def hook(tmp_path, monkeypatch):
    """The plugin, wired to the mock VibeCheck in-process (no network)."""
    monkeypatch.setattr(plugin, "LOG_PATH", tmp_path / "log.db")
    plugin.app.state.transport = _SyncASGI(mock_vibecheck.app)
    with TestClient(plugin.app) as c:
        yield c
    plugin.app.state.transport = None


class _SyncASGI(httpx.BaseTransport):
    """Route the plugin's outgoing httpx calls into another FastAPI app."""

    def __init__(self, target, fail_paths=()):
        self.client, self.fail_paths = TestClient(target), tuple(fail_paths)

    def handle_request(self, request):
        if any(p in request.url.path for p in self.fail_paths):
            raise httpx.ConnectError("down", request=request)
        r = self.client.request(request.method, request.url.path, content=request.content, headers=dict(request.headers))
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})


def event(**over):
    e = {**mock_vibecheck.SAMPLE_EVENT, "score": 61}
    e.update(over)
    return e


# ------------------------------------------------------------------ engine
@pytest.mark.parametrize("a,b,expected", [          # Sharma et al. (2005) reference pairs
    ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
    ((50, 2.5, 0), (50, 0, -2.5), 4.3065),
    ((50, 2.5, 0), (73, 25, -18), 27.1492),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
])
def test_ciede2000_reference_values(a, b, expected):
    assert ciede2000(a, b) == pytest.approx(expected, abs=1e-4)
    assert ciede2000(b, a) == pytest.approx(expected, abs=1e-4)


def test_clean_palette_validates_hex():
    good, bad = fixer.clean_palette(["#abc", "0B3D91", {"hex": "#fff"}, "#ABC", "blue", None, "#12345"])
    assert good == ["#AABBCC", "#0B3D91", "#FFFFFF"] and len(bad) == 3


SIG = ["#0B3D91", "#F2A900", "#FFFFFF", "#1B1B1B", "#D7263D"]


def test_swaps_only_off_brand_colours_and_keeps_them_distinct():
    plan = ["#1446A0", "#7BC950", "#FFFFFF", "#222222", "#F7B32B"]
    fix = fixer.propose_fix(plan, SIG)
    swapped = {s["from"]: s for s in fix["swaps"]}
    assert set(swapped) == {"#7BC950"}                      # near-brand blue/yellow/black are left alone
    assert fix["proposed"][2:4] == ["#FFFFFF", "#222222"]
    assert len(set(fix["proposed"])) == len(plan)
    assert swapped["#7BC950"]["delta_e_after"] < swapped["#7BC950"]["delta_e_before"]
    assert fix["mean_delta_e_after"] < fix["mean_delta_e_before"]


def test_swap_never_breaks_text_contrast():
    # Off-brand text colour; nearest signature colours by hue are light, which would kill contrast on white.
    plan, sig = ["#FFFFFF", "#7A3E9D"], ["#FFFFFF", "#F2C6FF", "#FFE08A"]
    before = fixer._contrast(*plan)
    fix = fixer.propose_fix(plan, sig, foreground="#7A3E9D", background="#FFFFFF")
    assert fix["contrast"]["after"] >= min(before, 4.5)
    for s in fix["swaps"]:
        assert s["kind"] == "signature hue at the original lightness"
    assert fix["swaps"] or fix["skipped"]


def test_limits_number_of_swaps():
    plan = ["#00FF00", "#00FFFF", "#FF00FF", "#808000", "#008080"]
    fix = fixer.propose_fix(plan, ["#0B3D91", "#F2A900", "#D7263D", "#1B1B1B", "#FFFFFF", "#5B2A86"])
    assert len(fix["swaps"]) == fixer.MAX_SWAPS and fix["skipped"]


def test_on_brand_palette_is_left_alone():
    fix = fixer.propose_fix(SIG[:4], SIG)
    assert fix["swaps"] == [] and fix["proposed"] == SIG[:4] and fix["mismatches"] == 0


# ------------------------------------------------------------------ service
def _texts(panel):
    return " ".join(str(b.get("text", "")) + str(b.get("value", "")) for b in panel["blocks"])


def test_hook_full_round_trip(hook):
    panel = hook.post("/hook", json=event()).json()
    assert panel["title"] == "Palette Fixer & Re-Scorer"
    kinds = [b["type"] for b in panel["blocks"]]
    assert kinds.count("swatches") == 2 and "table" in kinds
    stats = {b["label"]: b["value"] for b in panel["blocks"] if b["type"] == "stat"}
    assert stats["Original VibeCheck score"] == "61"
    new_score = int(stats["Score returned for the suggested palette"])
    assert new_score == mock_vibecheck.score("t1", [c["hex"] for c in panel["blocks"][2]["colors"]])
    assert "not a prediction" in _texts(panel) and "Passes WCAG AA" in _texts(panel)
    run = hook.get("/runs").json()[0]                       # recorded locally
    assert run["status"] == "ok" and run["check_id"] == "demo-1" and run["payload"]["fix"]["swaps"]


def test_hook_fallbacks(hook):
    assert "no usable colours" in _texts(hook.post("/hook", json=event(plan={"palette": ["nope"]})).json())
    assert "no api_base" in _texts(hook.post("/hook", json=event(api_base="")).json())
    assert "Ignored event" in _texts(hook.post("/hook", json=event(event="check.started")).json())
    assert "no signature palette" in _texts(hook.post("/hook", json=event(team={"id": "unknown"})).json())

    plugin.app.state.transport = _SyncASGI(mock_vibecheck.app, fail_paths=["/palette"])
    panel = hook.post("/hook", json=event()).json()
    assert "could not be retrieved" in _texts(panel) and panel["blocks"][0]["level"] == "warn"

    plugin.app.state.transport = _SyncASGI(mock_vibecheck.app, fail_paths=["/score"])
    panel = hook.post("/hook", json=event()).json()
    assert "Re-scoring is unavailable" in _texts(panel)
    assert [b["type"] for b in panel["blocks"]].count("swatches") == 2      # suggestions still shown
    assert {r["status"] for r in hook.get("/runs").json()} >= {"rescore_unavailable", "palette_unavailable", "no_palette"}


def test_runs_inside_host_without_any_server(tmp_path, monkeypatch):
    """In-app mode: the host calls handle_event directly with its own functions. No HTTP, no second process."""
    monkeypatch.setattr(plugin, "LOG_PATH", tmp_path / "log.db")
    calls = []

    def host_rescore(team_id, palette, check_id):
        calls.append((team_id, check_id))
        return {"score": mock_vibecheck.score(team_id, palette)}

    e = event(api_base="")                                  # no REST address at all
    panel = plugin.handle_event(e, team_palette=lambda team_id: mock_vibecheck.SIGNATURE[team_id], rescore=host_rescore)
    stats = {b["label"]: b["value"] for b in panel["blocks"] if b["type"] == "stat"}
    assert calls == [("t1", "demo-1")] and int(stats["Score returned for the suggested palette"]) > 61

    def broken(team_id):
        raise KeyError(team_id)

    assert "could not be retrieved" in _texts(plugin.handle_event(e, team_palette=broken, rescore=host_rescore))


def test_panel_matches_paletteai_panel_schema(hook):
    from paletteai.schemas import Panel
    Panel.model_validate(hook.post("/hook", json=event()).json())
