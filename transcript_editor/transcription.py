from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
EDITOR_OUTPUT_DIR = OUTPUT_DIR / "editor_audio"


def _run_transcribe_assemblyai(input_path: Path, speakers_expected: int | None, speech_threshold: float | None, user_email: str | None) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    EDITOR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    temp_input = EDITOR_OUTPUT_DIR / f"{uuid.uuid4().hex}{input_path.suffix}"
    if input_path != temp_input:
        temp_input.write_bytes(input_path.read_bytes())

    cmd = [sys.executable, "transcribe_assemblyai.py", str(temp_input)]
    if speakers_expected is not None:
        cmd += ["--speakers", str(speakers_expected)]
    if speech_threshold is not None:
        cmd += ["--speech-threshold", str(speech_threshold)]

    env = os.environ.copy()
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        env[key.strip()] = value.strip().strip('"').strip("'")
        except Exception:
            pass

    if user_email:
        env["VOCABULARY_USER_EMAIL"] = user_email

    result = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Transcription failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )

    utter_path = OUTPUT_DIR / f"{temp_input.stem}_utterances.json"
    if not utter_path.exists():
        raise RuntimeError(f"Expected AssemblyAI utterances at {utter_path}")
    return utter_path


def transcribe_and_parse(
    audio_path: Path,
    speakers_expected: int | None,
    speech_threshold: float | None,
    user_email: str | None = None,
) -> list[dict]:
    utter_path = _run_transcribe_assemblyai(audio_path, speakers_expected, speech_threshold, user_email)
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
