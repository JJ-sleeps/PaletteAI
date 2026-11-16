"""Pydantic models: these drive the published OpenAPI document (/docs, /openapi.json)."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, HttpUrl

USER_PATTERN = r"^[A-Za-z0-9_.-]{1,40}$"
UserId = Annotated[str, Field(pattern=USER_PATTERN, description="Profile name. No password; it only separates libraries and feedback.", examples=["guest"])]
Hex = Annotated[str, Field(description="HEX colour, #RGB or #RRGGBB", examples=["#2A78D6"])]
CvdType = Literal["protanopia", "deuteranopia", "tritanopia"]
Slot = Literal["palette.result", "library"]


class ErrorBody(BaseModel):
    code: str = Field(examples=["invalid_image"])
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorBody


# ---------------------------------------------------------------- colours
class ColorInfo(BaseModel):
    hex: str = Field(examples=["#2A78D6"])
    rgb: list[int] = Field(examples=[[42, 120, 214]])
    name: str = Field(description="Plain-language colour description", examples=["blue"])
    text_color: str = Field(description="Black or white, whichever reads better on this colour")


class PaletteColor(ColorInfo):
    lab: list[float] = Field(description="CIELAB (D65) coordinates [L, a, b]")
    share: float = Field(description="Fraction of sampled pixels in this cluster (0-1)")
    percent: float
    cvd: dict[str, str] = Field(description="HEX as seen with each colour-vision deficiency")


class ImageInfo(BaseModel):
    id: int
    filename: str
    mime: str
    width: int
    height: int
    size_bytes: int
    source: Literal["upload", "sample"]
    has_alpha: bool
    transparent_fraction: float
    content_hash: str = Field(description="SHA-256 of the uploaded bytes; used to skip repeat extraction")
    preview_url: str


class KScore(BaseModel):
    k: int
    silhouette: float
    chosen: bool


class ContrastPair(BaseModel):
    a: int = Field(description="Index into palette.colors")
    b: int
    a_hex: str
    b_hex: str
    ratio: float
    aa_normal_text: bool
    aa_large_text: bool
    aaa_normal_text: bool


class Accessibility(BaseModel):
    pairs: list[ContrastPair]
    total_pairs: int
    aa_normal_pairs: int
    aa_large_pairs: int
    best_pair: ContrastPair | None
    on_white: list[float]
    on_black: list[float]


class Palette(BaseModel):
    id: int
    image: ImageInfo
    k: int = Field(description="Number of colours in the palette")
    method: Literal["auto", "manual"]
    requested_k: int | None
    silhouette: float | None
    k_scores: list[KScore] = Field(description="Silhouette score for every candidate k that was tried")
    fallback_used: bool = Field(description="True when silhouette was inconclusive and the default k was used")
    reason: str = Field(description="Plain-language explanation of how k was chosen")
    colors: list[PaletteColor]
    features: dict[str, float] = Field(description="Feature vector used by the preference-ranking model")
    accessibility: Accessibility
    saved: bool
    saved_name: str | None
    feedback: Literal["like", "dislike"] | None
    created_at: str


class ExtractionResult(BaseModel):
    cached: bool = Field(description="True when an identical upload was served from the database")
    palette: Palette


class SampleImage(BaseModel):
    id: str
    name: str
    description: str
    url: str


# ---------------------------------------------------------------- requests
class SaveRequest(BaseModel):
    user: UserId = "guest"
    name: str | None = Field(None, max_length=80, description="Display name; defaults to the image file name")


class FeedbackRequest(BaseModel):
    user: UserId = "guest"
    value: Literal["like", "dislike"]


class ContrastRequest(BaseModel):
    foreground: Hex
    background: Hex
    model_config = {"json_schema_extra": {"example": {"foreground": "#1C5CAB", "background": "#FFFFFF"}}}


class ContrastCheck(BaseModel):
    id: str
    label: str
    threshold: float
    passed: bool
    verdict: Literal["Pass", "Fail"]


class ContrastResponse(BaseModel):
    foreground: str
    background: str
    ratio: float
    checks: list[ContrastCheck]


class HarmonyRequest(BaseModel):
    color: Hex


class HarmonyScheme(BaseModel):
    id: str
    name: str
    description: str
    colors: list[ColorInfo]


class HarmonyResponse(BaseModel):
    base: ColorInfo
    schemes: list[HarmonyScheme]


class CvdRequest(BaseModel):
    colors: list[Hex] = Field(min_length=1, max_length=64)
    types: list[CvdType] | None = Field(None, description="Defaults to all three types")
    model_config = {"json_schema_extra": {"example": {"colors": ["#E34948", "#008300"], "types": ["deuteranopia"]}}}


class CvdColor(BaseModel):
    original: ColorInfo
    simulated: dict[str, ColorInfo]


class CvdResponse(BaseModel):
    types: list[str]
    colors: list[CvdColor]


# ---------------------------------------------------------------- ranking
class RankingModel(BaseModel):
    status: Literal["cold_start", "personalised"]
    liked: int
    disliked: int
    feature_weights: dict[str, float]
    liked_centroid: dict[str, float] | None
    disliked_centroid: dict[str, float] | None
    feature_labels: dict[str, str]


class RankedItem(BaseModel):
    rank: int
    score: float = Field(description="like_similarity - dislike_similarity, in [-1, 1]")
    like_similarity: float
    dislike_similarity: float
    reasons: list[str]
    palette: Palette


class RankingResponse(BaseModel):
    user: str
    scope: Literal["saved", "all"]
    model: RankingModel
    items: list[RankedItem]


# ---------------------------------------------------------------- plugin panels
class TextBlock(BaseModel):
    type: Literal["text"]
    text: str = Field(max_length=2000)


class StatusBlock(BaseModel):
    type: Literal["status"]
    level: Literal["info", "ok", "warn", "fail"]
    text: str = Field(max_length=500)


class StatBlock(BaseModel):
    type: Literal["stat"]
    label: str = Field(max_length=80)
    value: str = Field(max_length=40)


class Swatch(BaseModel):
    hex: Hex
    label: str | None = Field(None, max_length=80)


class SwatchesBlock(BaseModel):
    type: Literal["swatches"]
    label: str | None = Field(None, max_length=120)
    colors: list[Swatch] = Field(max_length=24)


class TableBlock(BaseModel):
    type: Literal["table"]
    caption: str = Field(max_length=160)
    columns: list[str] = Field(min_length=1, max_length=8)
    rows: list[list[str]] = Field(max_length=50)


class BarItem(BaseModel):
    label: str = Field(max_length=80)
    value: float
    hex: Hex | None = None


class BarsBlock(BaseModel):
    type: Literal["bars"]
    caption: str = Field(max_length=160)
    unit: str = Field("", max_length=12)
    items: list[BarItem] = Field(max_length=24)


Block = Annotated[
    Union[TextBlock, StatusBlock, StatBlock, SwatchesBlock, TableBlock, BarsBlock],
    Field(discriminator="type"),
]


class Panel(BaseModel):
    """What a plugin returns. The host renders it natively inside the app."""

    title: str = Field(max_length=80)
    blocks: list[Block] = Field(max_length=20)
    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Tints",
                "blocks": [
                    {"type": "text", "text": "Lighter versions of the dominant colour."},
                    {"type": "swatches", "colors": [{"hex": "#86B6EF", "label": "tint 1"}]},
                ],
            }
        }
    }


class PanelRequest(BaseModel):
    """Body the host POSTs to a webhook plugin's ``hook_url``."""

    event: Literal["panel.requested"] = "panel.requested"
    slot: Slot
    user: str
    api_base: str = Field(description="Base URL of this PaletteAI API, for call-backs")
    palette: Palette | None = Field(None, description="Present for the palette.result slot")


class PanelResult(BaseModel):
    plugin: str
    kind: Literal["local", "webhook"]
    ok: bool
    elapsed_ms: int
    panel: Panel | None = None
    error: str | None = None


class PluginRegistration(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9 _.-]{2,40}$", examples=["Best text pairs"])
    description: str = Field("", max_length=200)
    hook_url: HttpUrl = Field(description="PaletteAI POSTs a PanelRequest here and expects a Panel back within 3 s", examples=["http://127.0.0.1:8001/hook"])
    slots: list[Slot] = Field(["palette.result"], min_length=1)


class PluginInfo(BaseModel):
    id: int | None = Field(None, description="Set for webhook plugins (use it to unregister)")
    name: str
    description: str
    kind: Literal["local", "webhook"]
    slots: list[str]
    hook_url: str | None = None
    routes_prefix: str | None = Field(None, description="Where a local plugin's own API routes are mounted")
    enabled: bool = True
    error: str | None = None
