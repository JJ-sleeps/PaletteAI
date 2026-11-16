"""Plugin system: lets another team extend PaletteAI without touching the core.

Two ways to plug in (see docs/PLUGINS.md):

* **Local plugin** - a folder ``plugins/<name>/plugin.py`` exposing
  ``register(host)``. It can add its own API routes and result panels, and runs
  inside the app process.
* **Webhook plugin** - any HTTP service registered via ``POST /api/v1/plugins``.
  PaletteAI POSTs a ``PanelRequest`` to its hook URL and renders the returned
  ``Panel`` inside the app.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
import traceback
from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI
from pydantic import ValidationError

from . import color as C
from . import db, service
from .config import API_PREFIX, Settings
from .schemas import Panel

SLOTS = ("palette.result", "library")
HOOK_TIMEOUT_SECONDS = 3.0


class HostAPI:
    """Read-only access to PaletteAI data for local plugins (no direct DB access needed)."""

    def __init__(self, settings: Settings):
        self._settings = settings

    def _conn(self) -> sqlite3.Connection:
        return db.connect(self._settings.db_path)

    def get_palette(self, palette_id: int, user: str = "guest") -> dict:
        conn = self._conn()
        try:
            return service.get_palette(conn, palette_id, user)
        finally:
            conn.close()

    def list_palettes(self, user: str = "guest", saved_only: bool = False) -> list[dict]:
        conn = self._conn()
        try:
            return service.list_palettes(conn, user, saved_only)
        finally:
            conn.close()

    def ranking(self, user: str = "guest", scope: str = "saved") -> dict:
        conn = self._conn()
        try:
            return service.rank_palettes(conn, user, scope)
        finally:
            conn.close()

    # colour utilities
    contrast = staticmethod(C.wcag_report)
    harmonies = staticmethod(C.harmonies)
    simulate_cvd = staticmethod(C.simulate_cvd_hex)
    describe = staticmethod(C.describe)


class PluginHost:
    """Handed to a local plugin's ``register(host)`` function."""

    def __init__(self, name: str, manager: "PluginManager"):
        self.name = name
        self.description = ""
        self.api = HostAPI(manager.settings)
        self.routes_prefix = f"{API_PREFIX}/ext/{name}"
        self._manager = manager
        self._panels: dict[str, Callable[[dict], dict]] = {}
        self._has_routes = False

    def describe(self, description: str) -> None:
        self.description = description[:200]

    def add_router(self, router: APIRouter) -> None:
        """Mount the plugin's routes under ``/api/v1/ext/<plugin-name>``; they appear in /docs."""
        self._manager.app.include_router(router, prefix=self.routes_prefix, tags=[f"plugin: {self.name}"])
        self._has_routes = True

    def add_panel(self, slot: str, provider: Callable[[dict], dict]) -> None:
        """Register ``provider(context) -> Panel dict`` for a UI slot.

        ``context`` has ``slot``, ``user``, ``api_base`` and (for palette.result) ``palette``.
        """
        if slot not in SLOTS:
            raise ValueError(f"Unknown slot '{slot}'. Available: {', '.join(SLOTS)}")
        self._panels[slot] = provider


class PluginManager:
    def __init__(self, settings: Settings, app: FastAPI):
        self.settings = settings
        self.app = app
        self.local: list[PluginHost] = []
        self.load_errors: dict[str, str] = {}

    # ------------------------------------------------------------ local plugins
    def load_local(self) -> None:
        folder: Path = self.settings.plugins_dir
        if not folder.is_dir():
            return
        for entry in sorted(folder.iterdir()):
            module_file = entry / "plugin.py"
            if not module_file.is_file() or entry.name.startswith(("_", ".")):
                continue
            host = PluginHost(entry.name, self)
            try:
                spec = importlib.util.spec_from_file_location(f"paletteai_plugin_{entry.name}", module_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.register(host)
                self.local.append(host)
            except Exception as exc:  # a broken plugin must not take the app down
                self.load_errors[entry.name] = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()

    # ------------------------------------------------------------ listing
    def list(self, conn: sqlite3.Connection) -> list[dict]:
        out = [
            {"id": None, "name": h.name, "description": h.description, "kind": "local", "slots": sorted(h._panels),
             "hook_url": None, "routes_prefix": h.routes_prefix if h._has_routes else None, "enabled": True, "error": None}
            for h in self.local
        ]
        out += [
            {"id": None, "name": name, "description": "", "kind": "local", "slots": [], "hook_url": None,
             "routes_prefix": None, "enabled": False, "error": err}
            for name, err in self.load_errors.items()
        ]
        for row in conn.execute("SELECT * FROM plugins ORDER BY id"):
            out.append({"id": row["id"], "name": row["name"], "description": row["description"], "kind": "webhook",
                        "slots": json.loads(row["slots_json"]), "hook_url": row["hook_url"], "routes_prefix": None,
                        "enabled": bool(row["enabled"]), "error": None})
        return out

    # ------------------------------------------------------------ panels
    @staticmethod
    def _validated(raw) -> dict:
        return Panel.model_validate(raw).model_dump()

    def panels(self, conn: sqlite3.Connection, slot: str, context: dict) -> list[dict]:
        """Ask every plugin subscribed to ``slot`` for a panel. Failures become error entries."""
        results = []
        jobs: list[tuple[str, str, Callable[[], object]]] = []
        for host in self.local:
            if slot in host._panels:
                jobs.append((host.name, "local", lambda p=host._panels[slot]: p(dict(context))))
        for row in conn.execute("SELECT * FROM plugins WHERE enabled = 1 ORDER BY id"):
            if slot in json.loads(row["slots_json"]):
                jobs.append((row["name"], "webhook", lambda url=row["hook_url"]: self._call_hook(url, context)))
        for name, kind, job in jobs:
            start = time.perf_counter()
            entry = {"plugin": name, "kind": kind, "ok": False, "panel": None, "error": None}
            try:
                entry["panel"] = self._validated(job())
                entry["ok"] = True
            except ValidationError as exc:
                entry["error"] = f"Plugin returned an invalid panel ({exc.error_count()} problem(s))."
            except httpx.TimeoutException:
                entry["error"] = f"Plugin did not answer within {HOOK_TIMEOUT_SECONDS:g} seconds."
            except httpx.HTTPError as exc:
                entry["error"] = f"Plugin could not be reached: {type(exc).__name__}."
            except Exception as exc:
                entry["error"] = f"Plugin failed: {type(exc).__name__}: {exc}"[:300]
            entry["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
            results.append(entry)
        return results

    @staticmethod
    def _call_hook(url: str, context: dict):
        payload = {"event": "panel.requested", **context}
        response = httpx.post(url, json=payload, timeout=HOOK_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
