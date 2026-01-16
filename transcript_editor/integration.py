from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import db


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def row_to_dict(row) -> dict:
    return dict(row) if row is not None else {}


def get_recording_by_original_path(original_path: str) -> dict | None:
    row = db.fetch_one("SELECT * FROM recordings WHERE original_path = ?", (original_path,))
    return row_to_dict(row)


def create_recording(filename: str, original_path: str | None, upload_path: str | None, status: str) -> str:
    recording_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO recordings (id, filename, original_path, upload_path, created_at, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (recording_id, filename, original_path, upload_path, now_iso(), status),
    )
    return recording_id


def set_recording_status(recording_id: str, status: str, error_message: str | None = None) -> None:
    db.execute(
        """
        UPDATE recordings SET status = ?, error_message = ? WHERE id = ?
        """,
        (status, error_message, recording_id),
    )


def set_current_version(recording_id: str, version_id: str) -> None:
    db.execute(
        """
        UPDATE recordings SET current_version_id = ? WHERE id = ?
        """,
        (version_id, recording_id),
    )


def create_version(recording_id: str, parent_version_id: str | None, note: str) -> str:
    version_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO versions (id, recording_id, parent_version_id, created_at, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        (version_id, recording_id, parent_version_id, now_iso(), note),
    )
    return version_id


def insert_utterances(version_id: str, utterances: Iterable[dict], source_version_id: str) -> None:
    rows = []
    for u in utterances:
        rows.append(
            (
                u.get("id", str(uuid.uuid4())),
                version_id,
                int(u.get("idx", 0)),
                int(u.get("start_ms", 0)),
                int(u.get("end_ms", 0)),
                u.get("speaker_label") or u.get("speaker") or "Unknown",
                u.get("raw_speaker") or u.get("raw_speaker_label"),
                float(u.get("confidence", 0)),
                u.get("confidence_source"),
                u.get("text", ""),
                int(u.get("is_manually_edited", 0)),
                u.get("source_version_id", source_version_id),
            )
        )

    db.executemany(
        """
        INSERT INTO utterances (id, version_id, idx, start_ms, end_ms, speaker, raw_speaker,
                               confidence, confidence_source, text, is_manually_edited, source_version_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _load_assemblyai_utterances(utter_path: Path) -> list[dict]:
    raw = json.loads(utter_path.read_text(encoding="utf-8"))
    utterances = []
    for idx, u in enumerate(raw):
        start_s = float(u.get("start") or 0)
        end_s = float(u.get("end") or 0)
        speaker = (u.get("speaker") or "Unknown").strip()
        text = (u.get("text") or "").strip()
        if not text:
            continue
        utterances.append(
            {
                "id": str(uuid.uuid4()),
                "idx": idx,
                "start_ms": int(start_s * 1000),
                "end_ms": int(end_s * 1000),
                "speaker_label": speaker,
                "raw_speaker": speaker,
                "confidence": 1.0,
                "confidence_source": "assemblyai",
                "text": text,
                "is_manually_edited": 0,
            }
        )
    return utterances


def import_assemblyai_utterances(audio_path: Path, utterances_path: Path) -> str:
    original_path = str(audio_path)
    existing = get_recording_by_original_path(original_path)

    if existing and existing.get("status") == "ready" and existing.get("current_version_id"):
        return existing["id"]

    if existing:
        recording_id = existing["id"]
        set_recording_status(recording_id, "processing")
    else:
        recording_id = create_recording(audio_path.name, original_path, None, "processing")

    utterances = _load_assemblyai_utterances(utterances_path)
    version_id = create_version(recording_id, None, "Auto import from meeting upload")
    insert_utterances(version_id, utterances, source_version_id=version_id)
    set_current_version(recording_id, version_id)
    set_recording_status(recording_id, "ready")
    return recording_id
