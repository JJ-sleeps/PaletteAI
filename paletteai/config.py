"""Runtime paths. Override the data folder with the PALETTEAI_DATA_DIR env var."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
API_PREFIX = "/api/v1"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    plugins_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paletteai.db"

    @property
    def previews_dir(self) -> Path:
        return self.data_dir / "previews"

    @property
    def samples_dir(self) -> Path:
        return self.data_dir / "samples"


def load_settings(data_dir: Path | str | None = None, plugins_dir: Path | str | None = None) -> Settings:
    data = Path(data_dir or os.environ.get("PALETTEAI_DATA_DIR") or PROJECT_DIR / "data")
    plugins = Path(plugins_dir or os.environ.get("PALETTEAI_PLUGINS_DIR") or PROJECT_DIR / "plugins")
    return Settings(data_dir=data, plugins_dir=plugins)
