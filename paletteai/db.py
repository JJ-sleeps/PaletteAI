"""SQLite persistence."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    mime TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    has_alpha INTEGER NOT NULL DEFAULT 0,
    transparent_fraction REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'upload',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS palettes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    requested_k INTEGER NOT NULL DEFAULT 0,          -- 0 = automatic
    k INTEGER NOT NULL,
    method TEXT NOT NULL,
    silhouette REAL,
    k_scores_json TEXT NOT NULL DEFAULT '{}',
    fallback_used INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    colors_json TEXT NOT NULL,
    features_json TEXT NOT NULL,
    accessibility_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (image_id, requested_k)
);
CREATE TABLE IF NOT EXISTS library (
    user_id TEXT NOT NULL,
    palette_id INTEGER NOT NULL REFERENCES palettes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (user_id, palette_id)
);
CREATE TABLE IF NOT EXISTS feedback (
    user_id TEXT NOT NULL,
    palette_id INTEGER NOT NULL REFERENCES palettes(id) ON DELETE CASCADE,
    value INTEGER NOT NULL CHECK (value IN (-1, 1)),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (user_id, palette_id)
);
CREATE TABLE IF NOT EXISTS plugins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    hook_url TEXT NOT NULL,
    slots_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
