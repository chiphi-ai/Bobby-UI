from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "output" / "editor_app.db"
DB_PATH = Path(os.environ.get("EDITOR_DB_PATH", str(DEFAULT_DB_PATH)))


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recordings (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            original_path TEXT,
            upload_path TEXT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            current_version_id TEXT,
            error_message TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS versions (
            id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL,
            parent_version_id TEXT,
            created_at TEXT NOT NULL,
            note TEXT,
            FOREIGN KEY(recording_id) REFERENCES recordings(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS utterances (
            id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            start_ms INTEGER NOT NULL,
            end_ms INTEGER NOT NULL,
            speaker TEXT NOT NULL,
            raw_speaker TEXT,
            confidence REAL NOT NULL,
            confidence_source TEXT,
            text TEXT NOT NULL,
            is_manually_edited INTEGER NOT NULL DEFAULT 0,
            source_version_id TEXT NOT NULL,
            FOREIGN KEY(version_id) REFERENCES versions(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS speaker_map (
            id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL,
            from_label TEXT NOT NULL,
            to_label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(recording_id) REFERENCES recordings(id)
        )
        """
    )

    conn.commit()
    conn.close()


def fetch_one(query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    conn = get_conn()
    cur = conn.execute(query, params)
    row = cur.fetchone()
    conn.close()
    return row


def fetch_all(query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    conn = get_conn()
    cur = conn.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def execute(query: str, params: Iterable[Any] = ()) -> None:
    conn = get_conn()
    conn.execute(query, params)
    conn.commit()
    conn.close()


def executemany(query: str, params: list[tuple]) -> None:
    conn = get_conn()
    conn.executemany(query, params)
    conn.commit()
    conn.close()
