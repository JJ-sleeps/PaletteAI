"""Application factory. Run with:  python run.py   (or: uvicorn paletteai.main:app)"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, samples
from .api import build_router
from .config import PACKAGE_DIR, load_settings
from .etl import IngestError
from .plugins import PluginManager
from .schemas import Panel, PanelRequest
from .service import NotFound

DESCRIPTION = """
PaletteAI extracts, explores, evaluates and organises colour palettes from images.

**For plugin developers:** start with the *plugins* section below and `docs/PLUGINS.md`.
Every error response has the shape `{"detail": {"code": "...", "message": "..."}}`.
Users are plain profile names (`user=guest`); there is no authentication because the app runs locally.
"""

TAGS = [
    {"name": "extraction", "description": "Image upload, validation and K-Means palette extraction."},
    {"name": "palettes", "description": "Stored palettes with coverage, contrast and colour-vision data."},
    {"name": "library", "description": "Per-user saved palettes."},
    {"name": "feedback & ranking", "description": "Like/dislike feedback and the adaptive preference ranking."},
    {"name": "colour tools", "description": "Stateless WCAG contrast, harmony and colour-vision utilities."},
    {"name": "plugins", "description": "Register webhook plugins and fetch the panels they contribute."},
    {"name": "system"},
]


def create_app(data_dir: Path | str | None = None, plugins_dir: Path | str | None = None) -> FastAPI:
    settings = load_settings(data_dir, plugins_dir)
    db.init_db(settings.db_path)
    settings.previews_dir.mkdir(parents=True, exist_ok=True)
    samples.ensure_samples(settings.samples_dir)

    app = FastAPI(title="PaletteAI", version="1.0.0", description=DESCRIPTION, openapi_tags=TAGS)
    manager = PluginManager(settings, app)
    app.state.settings = settings
    app.state.plugins = manager
    app.include_router(build_router(settings, manager))
    manager.load_local()

    @app.webhooks.post("panel.requested", response_model=Panel, tags=["plugins"],
                       summary="Sent to a webhook plugin's hook_url")
    def panel_requested(body: PanelRequest):
        """PaletteAI POSTs this to every registered webhook plugin subscribed to the slot being shown.
        Reply within 3 seconds with a `Panel`; it is rendered inside the app. Errors and time-outs
        are shown to the user as a small notice and never break the page."""

    def error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status)

    @app.exception_handler(IngestError)
    async def _ingest(_: Request, exc: IngestError):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(NotFound)
    async def _missing(_: Request, exc: NotFound):
        return error(404, "not_found", str(exc))

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        return error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path"))
        return error(422, "validation_error", f"{where}: {first.get('msg', 'invalid value')}" if where else first.get("msg", "Invalid request"))

    static_dir = PACKAGE_DIR / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.middleware("http")
    async def _revalidate_static(request: Request, call_next):
        # Browsers must check back before reusing a cached script or stylesheet, so an
        # edited app.js shows up on the next reload. Unchanged files still come back as
        # a cheap 304 thanks to StaticFiles' ETag.
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
