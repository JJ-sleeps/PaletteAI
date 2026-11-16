"""REST API (mounted at /api/v1). Documented automatically at /docs and /openapi.json."""
# NOTE: no `from __future__ import annotations` here - FastAPI must be able to
# resolve the `Conn` dependency alias that is defined inside build_router().
import io
import json
import sqlite3
from typing import Annotated, Literal

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from PIL import Image

from . import color as C
from . import db, samples, service
from . import schemas as S
from .config import API_PREFIX, Settings
from .etl import MAX_UPLOAD_BYTES, IngestError
from .plugins import PluginManager

UserQuery = Annotated[str, Query(pattern=S.USER_PATTERN, description="Profile name")]
KForm = Annotated[int | None, Form(ge=2, le=10, description="Number of colours. Omit for automatic selection by silhouette score.")]
KQuery = Annotated[int | None, Query(ge=2, le=10, description="Number of colours. Omit for automatic selection.")]
ERRORS = {code: {"model": S.ErrorResponse} for code in (400, 404, 413, 415, 422)}


def _hex(value: str) -> str:
    try:
        return C.normalize_hex(value)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_color", "message": str(exc)}) from None


def build_router(settings: Settings, plugins: PluginManager) -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    def get_conn():
        conn = db.connect(settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[sqlite3.Connection, Depends(get_conn)]

    # ------------------------------------------------------------ system
    @router.get("/health", tags=["system"], summary="Liveness check")
    def health(conn: Conn) -> dict:
        n = conn.execute("SELECT COUNT(*) FROM palettes").fetchone()[0]
        return {"status": "ok", "palettes": n}

    # ------------------------------------------------------------ extraction
    @router.post("/images", tags=["extraction"], response_model=S.ExtractionResult, responses=ERRORS,
                 summary="Upload an image and extract its dominant palette")
    async def upload_image(conn: Conn, file: Annotated[UploadFile, File(description="PNG, JPEG, WebP, GIF or BMP, up to 10 MB")],
                           k: KForm = None):
        """Validates the file, normalises it, clusters its pixels in CIELAB with K-Means and stores the result.

        Uploading the same bytes again with the same `k` returns the stored palette (`cached: true`).
        """
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        palette_id, cached = service.extract_palette(settings, conn, data, file.filename or "upload", "upload", k)
        return {"cached": cached, "palette": service.get_palette(conn, palette_id)}

    @router.get("/samples", tags=["extraction"], response_model=list[S.SampleImage], summary="List built-in sample images")
    def list_samples():
        return [{**s, "url": f"{API_PREFIX}/samples/{s['id']}/file"} for s in samples.list_samples()]

    def _sample_path(sample_id: str):
        if sample_id not in samples.SAMPLES:
            raise HTTPException(404, {"code": "not_found", "message": f"No sample image called '{sample_id}'."})
        return settings.samples_dir / f"{sample_id}.png"

    @router.get("/samples/{sample_id}/file", tags=["extraction"], responses={404: {"model": S.ErrorResponse}},
                response_class=FileResponse, summary="Download a sample image")
    def sample_file(sample_id: str):
        return FileResponse(_sample_path(sample_id), media_type="image/png")

    @router.post("/samples/{sample_id}/extract", tags=["extraction"], response_model=S.ExtractionResult,
                 responses=ERRORS, summary="Extract the palette of a built-in sample image")
    def extract_sample(conn: Conn, sample_id: str, k: KQuery = None):
        path = _sample_path(sample_id)
        palette_id, cached = service.extract_palette(settings, conn, path.read_bytes(), path.name, "sample", k)
        return {"cached": cached, "palette": service.get_palette(conn, palette_id)}

    @router.get("/images/{image_id}/preview", tags=["extraction"], responses={404: {"model": S.ErrorResponse}},
                response_class=Response, summary="Normalised preview of an image, optionally with a colour-vision simulation")
    def image_preview(conn: Conn, image_id: int, cvd: Annotated[S.CvdType | None, Query(description="Simulate this colour-vision deficiency")] = None):
        row = conn.execute("SELECT content_hash FROM images WHERE id = ?", (image_id,)).fetchone()
        path = settings.previews_dir / f"{row['content_hash']}.jpg" if row else None
        if not path or not path.is_file():
            raise HTTPException(404, {"code": "not_found", "message": f"Image {image_id} does not exist."})
        headers = {"Cache-Control": "private, max-age=3600"}
        if cvd is None:
            return Response(path.read_bytes(), media_type="image/jpeg", headers=headers)
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.float64)
        buf = io.BytesIO()
        Image.fromarray(C.simulate_cvd_rgb(arr, cvd).astype(np.uint8)).save(buf, "JPEG", quality=88)
        return Response(buf.getvalue(), media_type="image/jpeg", headers=headers)

    # ------------------------------------------------------------ palettes
    @router.get("/palettes", tags=["palettes"], response_model=list[S.Palette], summary="List palettes")
    def list_palettes(conn: Conn, user: UserQuery = "guest",
                      saved: Annotated[bool, Query(description="Only palettes in this user's library")] = False,
                      limit: Annotated[int, Query(ge=1, le=500)] = 100):
        return service.list_palettes(conn, user, saved, limit)

    @router.get("/palettes/{palette_id}", tags=["palettes"], response_model=S.Palette,
                responses={404: {"model": S.ErrorResponse}}, summary="Get one palette",
                description="Includes colours with coverage, colour-vision simulations, WCAG contrast for every colour pair, and this user's saved/feedback state.")
    def get_palette(conn: Conn, palette_id: int, user: UserQuery = "guest"):
        return service.get_palette(conn, palette_id, user)

    @router.put("/palettes/{palette_id}/save", tags=["library"], response_model=S.Palette,
                responses={404: {"model": S.ErrorResponse}}, summary="Save a palette to a user's library (or rename it)")
    def save_palette(conn: Conn, palette_id: int, body: S.SaveRequest):
        service.save_palette(conn, palette_id, body.user, body.name)
        return service.get_palette(conn, palette_id, body.user)

    @router.delete("/palettes/{palette_id}/save", tags=["library"], response_model=S.Palette,
                   responses={404: {"model": S.ErrorResponse}}, summary="Remove a palette from a user's library")
    def unsave_palette(conn: Conn, palette_id: int, user: UserQuery = "guest"):
        service.unsave_palette(conn, palette_id, user)
        return service.get_palette(conn, palette_id, user)

    @router.put("/palettes/{palette_id}/feedback", tags=["feedback & ranking"], response_model=S.Palette,
                responses={404: {"model": S.ErrorResponse}}, summary="Like or dislike a palette")
    def put_feedback(conn: Conn, palette_id: int, body: S.FeedbackRequest):
        """Feedback immediately updates the user's preference model used by `GET /ranking`."""
        service.set_feedback(conn, palette_id, body.user, body.value)
        return service.get_palette(conn, palette_id, body.user)

    @router.delete("/palettes/{palette_id}/feedback", tags=["feedback & ranking"], response_model=S.Palette,
                   responses={404: {"model": S.ErrorResponse}}, summary="Clear a user's feedback on a palette")
    def delete_feedback(conn: Conn, palette_id: int, user: UserQuery = "guest"):
        service.set_feedback(conn, palette_id, user, None)
        return service.get_palette(conn, palette_id, user)

    @router.get("/ranking", tags=["feedback & ranking"], response_model=S.RankingResponse,
                summary="Personalised palette ranking")
    def ranking(conn: Conn, user: UserQuery = "guest",
                scope: Annotated[Literal["saved", "all"], Query(description="Rank the user's library, or every palette")] = "saved"):
        """Ranks palettes by similarity to the user's liked palettes and dissimilarity from disliked ones.

        With no feedback yet (`model.status = cold_start`) palettes are returned newest first with score 0.
        """
        return service.rank_palettes(conn, user, scope)

    # ------------------------------------------------------------ colour tools
    @router.post("/contrast", tags=["colour tools"], response_model=S.ContrastResponse,
                 responses={422: {"model": S.ErrorResponse}}, summary="WCAG contrast check for a text/background pair")
    def contrast(body: S.ContrastRequest):
        return C.wcag_report(_hex(body.foreground), _hex(body.background))

    @router.post("/harmony", tags=["colour tools"], response_model=S.HarmonyResponse,
                 responses={422: {"model": S.ErrorResponse}}, summary="Colour-harmony suggestions for a base colour")
    def harmony(body: S.HarmonyRequest):
        base = _hex(body.color)
        return {"base": C.describe(base), "schemes": C.harmonies(base)}

    @router.post("/cvd/simulate", tags=["colour tools"], response_model=S.CvdResponse,
                 responses={422: {"model": S.ErrorResponse}}, summary="Simulate colour-vision deficiencies for a list of colours")
    def cvd_simulate(body: S.CvdRequest):
        types = list(dict.fromkeys(body.types or C.CVD_TYPES))
        return {
            "types": types,
            "colors": [
                {"original": C.describe(h), "simulated": {t: C.describe(C.simulate_cvd_hex(h, t)) for t in types}}
                for h in map(_hex, body.colors)
            ],
        }

    # ------------------------------------------------------------ plugins
    @router.get("/plugins", tags=["plugins"], response_model=list[S.PluginInfo], summary="List installed plugins")
    def list_plugins(conn: Conn):
        return plugins.list(conn)

    @router.post("/plugins", tags=["plugins"], response_model=S.PluginInfo, status_code=201,
                 responses={409: {"model": S.ErrorResponse}}, summary="Register a webhook plugin")
    def register_plugin(conn: Conn, body: S.PluginRegistration):
        """PaletteAI will POST a `PanelRequest` to `hook_url` whenever one of the chosen slots is shown,
        and render the `Panel` the plugin returns. See `PanelRequest` and `Panel` in the schemas below."""
        try:
            with conn:
                new_id = conn.execute(
                    "INSERT INTO plugins (name, description, hook_url, slots_json) VALUES (?,?,?,?)",
                    (body.name, body.description, str(body.hook_url), json.dumps(list(dict.fromkeys(body.slots)))),
                ).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409, {"code": "plugin_exists", "message": f"A plugin named '{body.name}' is already registered."}) from None
        return next(p for p in plugins.list(conn) if p["id"] == new_id)

    @router.delete("/plugins/{plugin_id}", tags=["plugins"], status_code=204, responses={404: {"model": S.ErrorResponse}},
                   summary="Unregister a webhook plugin")
    def unregister_plugin(conn: Conn, plugin_id: int):
        with conn:
            deleted = conn.execute("DELETE FROM plugins WHERE id = ?", (plugin_id,)).rowcount
        if not deleted:
            raise HTTPException(404, {"code": "not_found", "message": f"Plugin {plugin_id} does not exist."})
        return Response(status_code=204)

    @router.get("/panels", tags=["plugins"], response_model=list[S.PanelResult], responses={404: {"model": S.ErrorResponse}},
                summary="Collect plugin panels for a UI slot")
    def panels(request: Request, conn: Conn, slot: S.Slot = "palette.result", user: UserQuery = "guest",
               palette_id: Annotated[int | None, Query(description="Required for the palette.result slot")] = None):
        """Used by the web app to fill its plugin area. A plugin that errors or times out yields `ok: false`
        with a message instead of breaking the page."""
        context = {"slot": slot, "user": user, "api_base": str(request.base_url).rstrip("/") + API_PREFIX, "palette": None}
        if slot == "palette.result":
            if palette_id is None:
                raise HTTPException(422, {"code": "missing_palette", "message": "palette_id is required for the palette.result slot."})
            context["palette"] = service.get_palette(conn, palette_id, user)
        return plugins.panels(conn, slot, context)

    return router


__all__ = ["build_router", "IngestError"]
