from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, redirect, render_template, request, send_file, session, url_for

from . import db
from . import transcription

ROOT = Path(__file__).resolve().parents[1]
UPLOADS_DIR = ROOT / "output" / "editor_uploads"

editor_bp = Blueprint("transcript_editor", __name__, url_prefix="/editor")


def init_editor() -> None:
    db.init_db()
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_print(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def row_to_dict(row: Any) -> dict:
    return dict(row) if row is not None else {}


def create_recording(filename: str, original_path: str | None, upload_path: str | None) -> str:
    recording_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO recordings (id, filename, original_path, upload_path, created_at, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (recording_id, filename, original_path, upload_path, now_iso(), "uploaded"),
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


def insert_utterances(version_id: str, utterances: list[dict], source_version_id: str) -> None:
    rows = []
    for u in utterances:
        speaker = u.get("speaker_label") or u.get("speaker") or ""
        raw_speaker = u.get("raw_speaker") or u.get("raw_speaker_label")
        rows.append(
            (
                u.get("id", str(uuid.uuid4())),
                version_id,
                int(u.get("idx", 0)),
                int(u.get("start_ms", 0)),
                int(u.get("end_ms", 0)),
                speaker,
                raw_speaker,
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


def get_recording(recording_id: str) -> dict:
    row = db.fetch_one("SELECT * FROM recordings WHERE id = ?", (recording_id,))
    return row_to_dict(row)


def list_recordings() -> list[dict]:
    rows = db.fetch_all("SELECT * FROM recordings ORDER BY created_at DESC")
    return [dict(r) for r in rows]


def list_versions(recording_id: str) -> list[dict]:
    rows = db.fetch_all(
        "SELECT * FROM versions WHERE recording_id = ? ORDER BY created_at DESC",
        (recording_id,),
    )
    return [dict(r) for r in rows]


def get_version(version_id: str) -> dict:
    row = db.fetch_one("SELECT * FROM versions WHERE id = ?", (version_id,))
    return row_to_dict(row)


def get_utterances(version_id: str) -> list[dict]:
    rows = db.fetch_all(
        "SELECT * FROM utterances WHERE version_id = ? ORDER BY idx ASC",
        (version_id,),
    )
    return [dict(r) for r in rows]


def cluster_unknowns(utterances: list[dict]) -> None:
    if not utterances:
        return

    def get_label(utt: dict) -> str:
        return (utt.get("speaker_label") or utt.get("speaker") or "").strip()

    def set_label(utt: dict, label: str) -> None:
        if "speaker_label" in utt:
            utt["speaker_label"] = label
        else:
            utt["speaker"] = label

    unknowns = [(i, u) for i, u in enumerate(utterances) if get_label(u).startswith("Unknown Speaker")]
    if len(unknowns) <= 1:
        return

    clusters: list[list[tuple[int, dict]]] = []
    current = [unknowns[0]]

    for i, (idx, utt) in enumerate(unknowns[1:], 1):
        prev_idx, prev_utt = unknowns[i - 1]
        gap = (utt["start_ms"] - prev_utt["end_ms"]) / 1000.0
        if gap > 30.0:
            clusters.append(current)
            current = [(idx, utt)]
        else:
            current.append((idx, utt))
    clusters.append(current)

    for cluster_num, cluster in enumerate(clusters, 1):
        for idx, _ in cluster:
            set_label(utterances[idx], f"Unknown Speaker {cluster_num}")


def _require_login_redirect():
    if not session.get("user_email"):
        return redirect(url_for("login_get"))
    return None


def _require_login_json():
    if not session.get("user_email"):
        return jsonify({"error": "Login required"}), 401
    return None


@editor_bp.before_request
def log_request():
    if request.method in ["POST", "PUT", "PATCH", "DELETE"]:
        log_print("\n" + "=" * 60)
        log_print(f"[EDITOR REQUEST] {request.method} {request.path}")
        if request.is_json:
            log_print(f"[EDITOR REQUEST] JSON body: {request.json}")
        elif request.form:
            log_print(f"[EDITOR REQUEST] Form data: {dict(request.form)}")
        log_print("=" * 60)


@editor_bp.get("/")
def editor_index():
    redirect_resp = _require_login_redirect()
    if redirect_resp:
        return redirect_resp
    recordings = list_recordings()
    return render_template("editor/index.html", recordings=recordings)


@editor_bp.post("/upload")
def upload():
    unauthorized = _require_login_json()
    if unauthorized:
        return unauthorized

    file = request.files.get("audio_file")
    local_path = (request.form.get("local_path") or "").strip()
    filename = None
    upload_path = None

    if file and file.filename:
        filename = file.filename
        upload_path = str(UPLOADS_DIR / filename)
        file.save(upload_path)
    elif local_path:
        src = Path(local_path)
        if not src.exists():
            return jsonify({"error": f"Local path not found: {local_path}"}), 400
        filename = src.name
    else:
        return jsonify({"error": "Provide a file upload or local path"}), 400

    recording_id = create_recording(filename, local_path if local_path else None, upload_path)
    return jsonify({"recording_id": recording_id, "redirect": f"/editor/recording/{recording_id}"}), 201


@editor_bp.post("/process/<recording_id>")
def process_recording(recording_id: str):
    unauthorized = _require_login_json()
    if unauthorized:
        return unauthorized

    rec = get_recording(recording_id)
    if not rec:
        return jsonify({"error": "Recording not found"}), 404

    if rec["status"] == "processing":
        return jsonify({"error": "Recording is already processing"}), 409

    set_recording_status(recording_id, "processing")

    speakers_expected = request.json.get("speakers_expected") if request.is_json else None
    speech_threshold = request.json.get("speech_threshold") if request.is_json else None
    user_email = session.get("user_email")

    try:
        audio_path = rec.get("upload_path") or rec.get("original_path")
        if not audio_path:
            raise RuntimeError("No audio path available for recording")

        log_print(f"[EDITOR PROCESS] Transcribing {audio_path}")
        utterances = transcription.transcribe_and_parse(Path(audio_path), speakers_expected, speech_threshold, user_email)

        version_id = create_version(recording_id, None, "Base transcription")
        insert_utterances(version_id, utterances, source_version_id=version_id)
        set_current_version(recording_id, version_id)
        set_recording_status(recording_id, "ready")

        log_print(f"[EDITOR PROCESS] Done. Version {version_id} created")
        return jsonify({"recording_id": recording_id, "version_id": version_id}), 201

    except Exception as e:
        set_recording_status(recording_id, "error", str(e))
        log_print(f"[EDITOR PROCESS] ERROR: {e}")
        return jsonify({"error": str(e)}), 500


@editor_bp.get("/recording/<recording_id>/audio")
def recording_audio(recording_id: str):
    redirect_resp = _require_login_redirect()
    if redirect_resp:
        return redirect_resp

    rec = get_recording(recording_id)
    if not rec:
        return "Recording not found", 404

    audio_path = rec.get("upload_path") or rec.get("original_path")
    if not audio_path:
        return "No audio path available", 404

    path = Path(audio_path)
    if not path.exists():
        return "Audio file not found", 404

    ext = path.suffix.lower()
    mimetypes = {
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
    }
    return send_file(path, mimetype=mimetypes.get(ext), as_attachment=False)


@editor_bp.get("/recording/<recording_id>")
def view_recording(recording_id: str):
    redirect_resp = _require_login_redirect()
    if redirect_resp:
        return redirect_resp

    rec = get_recording(recording_id)
    if not rec:
        return "Recording not found", 404

    version_id = request.args.get("version_id") or rec.get("current_version_id")
    versions = list_versions(recording_id)

    utterances = get_utterances(version_id) if version_id else []
    speakers = sorted({u["speaker"] for u in utterances}) if utterances else []

    return render_template(
        "editor/recording.html",
        recording=rec,
        versions=versions,
        version_id=version_id,
        utterances=utterances,
        speakers=speakers,
    )


@editor_bp.get("/recording/<recording_id>/versions")
def recording_versions(recording_id: str):
    unauthorized = _require_login_json()
    if unauthorized:
        return unauthorized
    return jsonify({"versions": list_versions(recording_id)})


@editor_bp.get("/version/<version_id>")
def get_version_json(version_id: str):
    unauthorized = _require_login_json()
    if unauthorized:
        return unauthorized

    version = get_version(version_id)
    if not version:
        return jsonify({"error": "Version not found"}), 404
    utterances = get_utterances(version_id)
    return jsonify({"version": version, "utterances": utterances})


@editor_bp.post("/version/<version_id>/restore")
def restore_version(version_id: str):
    unauthorized = _require_login_json()
    if unauthorized:
        return unauthorized

    version = get_version(version_id)
    if not version:
        return jsonify({"error": "Version not found"}), 404

    set_current_version(version["recording_id"], version_id)
    return jsonify({"recording_id": version["recording_id"], "current_version_id": version_id})


@editor_bp.post("/version/<version_id>/bulk_edit")
def bulk_edit(version_id: str):
    unauthorized = _require_login_json()
    if unauthorized:
        return unauthorized

    version = get_version(version_id)
    if not version:
        return jsonify({"error": "Version not found"}), 404

    payload = request.json if request.is_json else {}
    utterance_rows = payload.get("utterances")
    edits = payload.get("edits", [])

    if not utterance_rows and not edits:
        return jsonify({"error": "No edits provided"}), 400

    base_utterances = get_utterances(version_id)
    base_map = {u["id"]: u for u in base_utterances}

    new_version_id = create_version(version["recording_id"], version_id, "Bulk edit")
    new_utterances = []

    if utterance_rows:
        for idx, row in enumerate(utterance_rows):
            row_id = row.get("id")
            speaker = (row.get("speaker") or "").strip()
            text = (row.get("text") or "").strip()
            start_ms = int(row.get("start_ms") or 0)
            end_ms = int(row.get("end_ms") or 0)
            is_new = bool(row.get("is_new"))

            base = base_map.get(row_id) if row_id and not is_new else None

            if base:
                is_edited = int((text != base["text"]) or (speaker != base["speaker"]))
                new_utterances.append(
                    {
                        "id": str(uuid.uuid4()),
                        "idx": idx,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "speaker_label": speaker or base["speaker"],
                        "raw_speaker": base["raw_speaker"],
                        "confidence": base["confidence"],
                        "confidence_source": base["confidence_source"],
                        "text": text or base["text"],
                        "is_manually_edited": is_edited or base.get("is_manually_edited", 0),
                        "source_version_id": base["source_version_id"],
                    }
                )
            else:
                if not speaker or not text:
                    continue
                new_utterances.append(
                    {
                        "id": str(uuid.uuid4()),
                        "idx": idx,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "speaker_label": speaker,
                        "raw_speaker": speaker,
                        "confidence": 1.0,
                        "confidence_source": "manual",
                        "text": text,
                        "is_manually_edited": 1,
                        "source_version_id": version_id,
                    }
                )
    else:
        edit_map = {e["id"]: e for e in edits}
        for u in base_utterances:
            e = edit_map.get(u["id"])
            text = e.get("text") if e else u["text"]
            speaker = e.get("speaker") if e else u["speaker"]
            is_edited = int((text != u["text"]) or (speaker != u["speaker"]))
            new_utterances.append(
                {
                    "id": str(uuid.uuid4()),
                    "idx": u["idx"],
                    "start_ms": u["start_ms"],
                    "end_ms": u["end_ms"],
                    "speaker_label": speaker,
                    "raw_speaker": u["raw_speaker"],
                    "confidence": u["confidence"],
                    "confidence_source": u["confidence_source"],
                    "text": text,
                    "is_manually_edited": is_edited or u.get("is_manually_edited", 0),
                    "source_version_id": u["source_version_id"],
                }
            )

    insert_utterances(new_version_id, new_utterances, source_version_id=version_id)
    set_current_version(version["recording_id"], new_version_id)
    return jsonify({"version_id": new_version_id})


@editor_bp.post("/version/<version_id>/rename_speaker")
def rename_speaker(version_id: str):
    unauthorized = _require_login_json()
    if unauthorized:
        return unauthorized

    version = get_version(version_id)
    if not version:
        return jsonify({"error": "Version not found"}), 404

    old_label = (request.json.get("old_label") or "").strip()
    new_label = (request.json.get("new_label") or "").strip()
    if not old_label or not new_label:
        return jsonify({"error": "old_label and new_label required"}), 400

    base_utterances = get_utterances(version_id)
    new_version_id = create_version(version["recording_id"], version_id, f"Rename {old_label} -> {new_label}")

    new_utterances = []
    for u in base_utterances:
        speaker = new_label if u["speaker"] == old_label else u["speaker"]
        is_edited = int(u["speaker"] != speaker) or u.get("is_manually_edited", 0)
        new_utterances.append(
            {
                "id": str(uuid.uuid4()),
                "idx": u["idx"],
                "start_ms": u["start_ms"],
                "end_ms": u["end_ms"],
                "speaker_label": speaker,
                "raw_speaker": u["raw_speaker"],
                "confidence": u["confidence"],
                "confidence_source": u["confidence_source"],
                "text": u["text"],
                "is_manually_edited": is_edited,
                "source_version_id": u["source_version_id"],
            }
        )

    insert_utterances(new_version_id, new_utterances, source_version_id=version_id)
    set_current_version(version["recording_id"], new_version_id)
    return jsonify({"version_id": new_version_id})


@editor_bp.post("/version/<version_id>/merge_speakers")
def merge_speakers(version_id: str):
    unauthorized = _require_login_json()
    if unauthorized:
        return unauthorized

    version = get_version(version_id)
    if not version:
        return jsonify({"error": "Version not found"}), 404

    label1 = (request.json.get("label1") or "").strip()
    label2 = (request.json.get("label2") or "").strip()
    target_label = (request.json.get("target_label") or "").strip()
    if not label1 or not label2 or not target_label:
        return jsonify({"error": "label1, label2, target_label required"}), 400

    base_utterances = get_utterances(version_id)
    new_version_id = create_version(version["recording_id"], version_id, f"Merge {label1}+{label2} -> {target_label}")

    new_utterances = []
    for u in base_utterances:
        speaker = u["speaker"]
        if speaker in [label1, label2]:
            speaker = target_label
        is_edited = int(u["speaker"] != speaker) or u.get("is_manually_edited", 0)

        new_utterances.append(
            {
                "id": str(uuid.uuid4()),
                "idx": u["idx"],
                "start_ms": u["start_ms"],
                "end_ms": u["end_ms"],
                "speaker_label": speaker,
                "raw_speaker": u["raw_speaker"],
                "confidence": u["confidence"],
                "confidence_source": u["confidence_source"],
                "text": u["text"],
                "is_manually_edited": is_edited,
                "source_version_id": u["source_version_id"],
            }
        )

    insert_utterances(new_version_id, new_utterances, source_version_id=version_id)
    set_current_version(version["recording_id"], new_version_id)
    return jsonify({"version_id": new_version_id})


@editor_bp.post("/version/<version_id>/split_speaker")
def split_speaker(version_id: str):
    unauthorized = _require_login_json()
    if unauthorized:
        return unauthorized

    version = get_version(version_id)
    if not version:
        return jsonify({"error": "Version not found"}), 404

    speaker_label = (request.json.get("speaker_label") or "").strip()
    new_label = (request.json.get("new_label") or "").strip()
    utterance_ids = request.json.get("utterance_ids") if request.is_json else None
    start_ms = request.json.get("start_ms") if request.is_json else None
    end_ms = request.json.get("end_ms") if request.is_json else None

    if not speaker_label or not new_label:
        return jsonify({"error": "speaker_label and new_label required"}), 400

    base_utterances = get_utterances(version_id)
    to_move = set(utterance_ids or [])

    if not to_move and start_ms is not None and end_ms is not None:
        for u in base_utterances:
            if u["speaker"] == speaker_label and start_ms <= u["start_ms"] <= end_ms:
                to_move.add(u["id"])

    if not to_move:
        return jsonify({"error": "No utterances selected for split"}), 400

    new_version_id = create_version(version["recording_id"], version_id, f"Split {speaker_label} -> {new_label}")

    new_utterances = []
    for u in base_utterances:
        speaker = new_label if u["id"] in to_move else u["speaker"]
        is_edited = int(u["speaker"] != speaker) or u.get("is_manually_edited", 0)

        new_utterances.append(
            {
                "id": str(uuid.uuid4()),
                "idx": u["idx"],
                "start_ms": u["start_ms"],
                "end_ms": u["end_ms"],
                "speaker_label": speaker,
                "raw_speaker": u["raw_speaker"],
                "confidence": u["confidence"],
                "confidence_source": u["confidence_source"],
                "text": u["text"],
                "is_manually_edited": is_edited,
                "source_version_id": u["source_version_id"],
            }
        )

    insert_utterances(new_version_id, new_utterances, source_version_id=version_id)
    set_current_version(version["recording_id"], new_version_id)
    return jsonify({"version_id": new_version_id})


@editor_bp.post("/version/<version_id>/recompute")
def recompute(version_id: str):
    unauthorized = _require_login_json()
    if unauthorized:
        return unauthorized

    version = get_version(version_id)
    if not version:
        return jsonify({"error": "Version not found"}), 404

    preserve_manual = request.json.get("preserve_manual", True) if request.is_json else True
    confidence_threshold = request.json.get("confidence_threshold", transcription.CONF_THRESHOLD) if request.is_json else transcription.CONF_THRESHOLD

    base_utterances = get_utterances(version_id)
    new_version_id = create_version(version["recording_id"], version_id, "Recompute labels")

    new_utterances = []
    for u in base_utterances:
        if preserve_manual and u.get("is_manually_edited"):
            speaker = u["speaker"]
        else:
            speaker = u["raw_speaker"] if u["confidence"] >= confidence_threshold else "Unknown Speaker 1"

        new_utterances.append(
            {
                "id": str(uuid.uuid4()),
                "idx": u["idx"],
                "start_ms": u["start_ms"],
                "end_ms": u["end_ms"],
                "speaker_label": speaker,
                "raw_speaker": u["raw_speaker"],
                "confidence": u["confidence"],
                "confidence_source": u["confidence_source"],
                "text": u["text"],
                "is_manually_edited": u.get("is_manually_edited", 0),
                "source_version_id": u["source_version_id"],
            }
        )

    cluster_unknowns(new_utterances)
    insert_utterances(new_version_id, new_utterances, source_version_id=version_id)
    set_current_version(version["recording_id"], new_version_id)
    return jsonify({"version_id": new_version_id})
