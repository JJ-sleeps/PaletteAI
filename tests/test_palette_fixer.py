"""Tests for the cross-dev plugin (Palette Fixer & Re-Scorer), against a mock with VibeCheck's real API shapes."""
import copy
from typing import Literal

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from crossdev.palette_fixer import app as plugin
from crossdev.palette_fixer import client as vc
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
        r = self.client.request(request.method, request.url.path, params=dict(request.url.params),
                                content=request.read(), headers=dict(request.headers))
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": r.headers.get("content-type", "")})


def event(**check_over):
    e = copy.deepcopy(mock_vibecheck.SAMPLE_EVENT)
    e["check"].update(check_over)
    return e


# A copy of VibeCheck's Panel schema (backend/vibe/schemas.py), so the tests fail if we drift from it.
class _Metric(BaseModel):
    label: str
    value: float | int | str
    unit: str | None = None


class _Item(BaseModel):
    text: str
    ok: bool | None = None


class _Point(BaseModel):
    label: str
    value: float


class _Chart(BaseModel):
    type: Literal["bar", "line"] = "bar"
    title: str | None = None
    x_label: str | None = None
    y_label: str | None = None
    points: list[_Point]


class VibePanel(BaseModel, extra="forbid"):
    title: str
    summary: str | None = None
    metrics: list[_Metric] | None = None
    items: list[_Item] | None = None
    chart: _Chart | None = None


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


def test_infer_roles_uses_largest_colour_as_background():
    fg, bg = fixer.infer_roles(["#FFFFFF", "#F2F2F2", "#222222", "#F7B32B"])
    assert bg == "#FFFFFF" and fg == "#222222"
    assert fixer.infer_roles(["#FFFFFF"]) == (None, None)


def test_recolour_moves_only_swapped_regions():
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    img[:, :2] = (255, 255, 255)
    img[:, 2:] = (123, 201, 80)                           # green half
    out = fixer.recolour(img, ["#FFFFFF", "#7BC950"], ["#FFFFFF", "#F2A900"])
    assert (out[:, :2] == 255).all()                      # white untouched
    assert fixer.C.rgb_to_hex(out[0, 3]) == "#F2A900"     # green became the brand yellow


# ------------------------------------------------------------------ service
def _texts(panel):
    return " ".join([panel.get("summary") or ""] + [i["text"] for i in panel.get("items") or []])


def _metrics(panel):
    return {m["label"]: m["value"] for m in panel.get("metrics") or []}


def test_parse_event_reads_vibecheck_shape():
    info = vc.parse_event(event())
    assert info["check_id"] == "42" and info["team_id"] == "1" and info["score"] == 61.0
    assert info["palette"][:2] == ["#FFFFFF", "#1446A0"] and info["inspo_urls"]
    assert info["colour_reasons"] and info["kind"] == "plan"


def test_hook_full_round_trip(hook):
    panel = hook.post("/hook", json=event()).json()
    VibePanel.model_validate(panel)                       # exactly VibeCheck's Panel, no unknown keys
    assert panel["title"] == "Palette Fixer & Re-Scorer"
    m = _metrics(panel)
    assert m["VibeCheck score now"] == 61.0
    assert m["Score with these swaps"] > mock_vibecheck.score_image("1", mock_vibecheck.demo_poster())
    assert "#7BC950" in _texts(panel) and "not our prediction" in _texts(panel)
    assert any(i.get("ok") is True and "WCAG AA" in i["text"] for i in panel["items"])
    assert panel["chart"]["points"][1]["value"] < panel["chart"]["points"][0]["value"]
    run = hook.get("/runs").json()[0]                     # recorded locally
    assert run["status"] == "ok" and run["check_id"] == "42" and run["payload"]["fix"]["swaps"]
    assert run["payload"]["roles"] == {"foreground": "#222222", "background": "#FFFFFF"}


def test_own_rescore_checks_are_not_processed_again(hook):
    panel = hook.post("/hook", json=event(description="x " + vc.RESCORE_TAG)).json()
    assert "re-score" in panel["summary"] and "metrics" not in panel
    assert hook.get("/runs").json()[0]["status"] == "own_rescore"


def test_hook_fallbacks(hook):
    assert "no colours" in _texts(hook.post("/hook", json=event(plan_palette=[{"hex": "nope"}])).json())
    e = event()
    e["api_base"] = ""
    assert "no api_base" in _texts(hook.post("/hook", json=e).json())
    e = event()
    e["event"] = "check.started"
    assert "Ignored event" in _texts(hook.post("/hook", json=e).json())
    e = event()
    e["team"] = {"id": 999}
    assert "no signature palette" in _texts(hook.post("/hook", json=e).json())
    assert "no images to recolour" in _texts(hook.post("/hook", json=event(inspo_urls=[])).json())

    plugin.app.state.transport = _SyncASGI(mock_vibecheck.app, fail_paths=["/style-profile"])
    panel = hook.post("/hook", json=event()).json()
    assert "the team's palette" in panel["summary"] and "Couldn" in panel["summary"]
    VibePanel.model_validate(panel)

    plugin.app.state.transport = _SyncASGI(mock_vibecheck.app, fail_paths=["/checks"])
    panel = hook.post("/hook", json=event()).json()
    assert "No re-score" in _texts(panel) and "Score with these swaps" not in _metrics(panel)
    assert "#7BC950" in _texts(panel)                     # suggestions still shown
    assert {r["status"] for r in hook.get("/runs").json()} >= {"rescore_unavailable", "palette_unavailable", "no_palette"}


def test_runs_inside_host_without_any_server(tmp_path, monkeypatch):
    """In-app mode: the host calls handle_event directly with its own functions. No HTTP, no second process."""
    monkeypatch.setattr(plugin, "LOG_PATH", tmp_path / "log.db")
    calls = []

    def host_rescore(team_id, palette, check_id):
        calls.append((team_id, check_id))
        return {"score": 88.0}

    e = event()
    e["api_base"] = ""                                    # no REST address at all
    panel = plugin.handle_event(e, team_palette=lambda team_id: mock_vibecheck.SIGNATURE[team_id], rescore=host_rescore)
    assert calls == [("1", "42")] and _metrics(panel)["Score with these swaps"] == 88.0

    def broken(team_id):
        raise KeyError(team_id)

    assert "Couldn" in plugin.handle_event(e, team_palette=broken, rescore=host_rescore)["summary"]
