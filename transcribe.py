import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

# -------------------------
# Helpers
# -------------------------
def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str]) -> None:
    """Run a subprocess, raising a helpful error if it fails."""
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as e:
        raise RuntimeError(
            f"Command not found: {cmd[0]}\n"
            f"Make sure it is installed and on PATH. (ffmpeg is required.)"
        ) from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Command failed:\n{' '.join(cmd)}") from e


def convert_to_wav_16k_mono(input_media: Path, wav_out: Path) -> None:
    # Uses ffmpeg from PATH
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_media),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(wav_out),
    ]
    run(cmd)


def _pick_token() -> str | None:
    return (
        os.environ.get("HF_TOKEN", "").strip()
        or os.environ.get("HUGGINGFACE_TOKEN", "").strip()
        or None
    )


def transcribe_with_whisper(wav_path: Path, model_name: str = "small", language: str | None = "en") -> dict:
    """
    Uses openai-whisper. Returns:
      {language, segments:[{start,end,text}], text}
    """
    try:
        import torch
    except Exception:
        torch = None

    try:
        import whisper
    except Exception as e:
        raise RuntimeError(
            "Missing dependency: openai-whisper. Install with: pip install -r requirements.txt\n"
            f"Import error: {e}"
        )

    device = os.getenv("WHISPER_DEVICE", "").strip().lower()
    if not device:
        device = "cpu"
        if torch is not None:
            if getattr(torch, "cuda", None) and torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = "mps"

    model = whisper.load_model(model_name, device=device)
    result = model.transcribe(
        str(wav_path),
        language=language or None,
        fp16=(device == "cuda"),
        verbose=False,
    )

    segments = []
    for s in (result.get("segments") or []):
        segments.append({
            "start": float(s.get("start", 0.0)),
            "end": float(s.get("end", 0.0)),
            "text": (s.get("text") or "").strip(),
        })

    full_text = " ".join([s["text"] for s in segments if s["text"]]).strip()
    return {
        "language": result.get("language") or "unknown",
        "segments": segments,
        "text": full_text,
    }


def diarize_with_pyannote(wav_path):
    print("3) Diarizing (pyannote)...")

    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    use_auth_token=_pick_token()
)

    diarization = pipeline(str(wav_path))

    segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append({
            "speaker": speaker,
            "start": round(turn.start, 2),
            "end": round(turn.end, 2),
        })

    return segments



def midpoint(a: float, b: float) -> float:
    return (a + b) / 2.0


def assign_speaker_to_transcript(transcript: dict, diar_segments: list[dict]) -> list[dict]:
    """
    Very simple alignment:
    For each transcript segment, find the diarization segment that contains the transcript midpoint.
    """
    diar_i = 0
    diar_n = len(diar_segments)

    utterances = []
    for seg in transcript.get("segments", []):
        s = float(seg["start"])
        e = float(seg["end"])
        m = midpoint(s, e)

        # advance diar pointer
        while diar_i < diar_n and diar_segments[diar_i]["end"] < m:
            diar_i += 1

        speaker = "UNKNOWN"
        if diar_i < diar_n:
            d = diar_segments[diar_i]
            if d["start"] <= m <= d["end"]:
                speaker = d["speaker"]

        utterances.append({
            "start": s,
            "end": e,
            "speaker": speaker,
            "text": seg.get("text", "")
        })

    return utterances


def write_rttm(diar_segments: list[dict], rttm_path: Path, file_id: str = "meeting") -> None:
    # Minimal RTTM writer
    # Format: SPEAKER <file-id> 1 <start> <duration> <ortho> <stype> <name> <conf> <slat>
    lines = []
    for d in diar_segments:
        start = d["start"]
        dur = max(0.0, d["end"] - d["start"])
        spk = d["speaker"]
        lines.append(f"SPEAKER {file_id} 1 {start:.3f} {dur:.3f} <NA> <NA> {spk} <NA> <NA>")
    rttm_path.write_text("\n".join(lines), encoding="utf-8")


# -------------------------
# Main
# -------------------------
def main() -> None:
    print("=== ENTERING MAIN ===", sys.argv)

    if len(sys.argv) < 2:
        print("Usage: python transcribe.py <input_media_file>")
        print("Example: python transcribe.py meeting.mp4")
        sys.exit(1)

    project_root = Path(__file__).resolve().parent
    input_dir = project_root / "input"
    output_dir = project_root / "output"
    ensure_dir(input_dir)
    ensure_dir(output_dir)

    in_name = sys.argv[1]
    input_path = Path(in_name)

    # If they passed "meeting.mp4" but file is actually in ./input, use that.
    if not input_path.exists():
        candidate = input_dir / in_name
        if candidate.exists():
            input_path = candidate

    if not input_path.exists():
        raise FileNotFoundError(
            f"Could not find input file:\n  {in_name}\n"
            f"Put it in:\n  {input_dir}\n"
            f"or pass a full path."
        )

    # Check ffmpeg availability early
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg was not found on PATH.\n"
            "Type: ffmpeg -version\n"
            "If that fails, you need to add ffmpeg/bin to PATH."
        )

    # Output names
    stem = input_path.stem
    wav_path = output_dir / f"{stem}_16k.wav"
    transcript_path = output_dir / f"{stem}_transcript.json"
    diar_path = output_dir / f"{stem}_diarization.json"
    rttm_path = output_dir / f"{stem}.rttm"
    utterances_path = output_dir / f"{stem}_utterances.json"

    print("1) Converting to WAV (16k mono)...")
    convert_to_wav_16k_mono(input_path, wav_path)
    print(f"   wrote: {wav_path}")

    print("2) Transcribing (Whisper)...")
    transcript = transcribe_with_whisper(
        wav_path,
        model_name=os.getenv("WHISPER_MODEL", "small").strip() or "small",
        language=os.getenv("WHISPER_LANGUAGE", "en").strip() or None,
    )
    transcript_path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    print(f"   wrote: {transcript_path}")

    print("3) Diarizing (pyannote)...")
    # If HF_TOKEN isn't set, diarization will likely fail for gated models.
    if not _pick_token():
        print("   WARNING: HF_TOKEN / HUGGINGFACE_TOKEN is not set. Diarization model download will fail.")

    diar_segments = diarize_with_pyannote(wav_path)
    diar_path.write_text(json.dumps(diar_segments, indent=2), encoding="utf-8")
    write_rttm(diar_segments, rttm_path, file_id=stem)
    print(f"   wrote: {diar_path}")
    print(f"   wrote: {rttm_path}")

    print("4) Aligning transcript + diarization into utterances...")
    utterances = assign_speaker_to_transcript(transcript, diar_segments)
    utterances_path.write_text(json.dumps(utterances, indent=2), encoding="utf-8")
    print(f"   wrote: {utterances_path}")

    print("\nDONE ✅")
    print(f"Open output folder:\n  {output_dir}")


if __name__ == "__main__":
    main()
