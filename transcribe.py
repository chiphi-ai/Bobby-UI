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
        word_timestamps=True,  # Enable word-level timestamps
    )

    segments = []
    segment_start_offset = 0.0
    for s in (result.get("segments") or []):
        seg_start = float(s.get("start", 0.0))
        seg_end = float(s.get("end", 0.0))
        seg_text = (s.get("text") or "").strip()
        
        # Extract word-level timestamps if available
        words = []
        if "words" in s and isinstance(s["words"], list):
            for w in s["words"]:
                word_text = (w.get("word") or "").strip()
                if word_text:
                    # Word timestamps are relative to segment start, convert to absolute
                    word_start = float(w.get("start", 0.0)) + seg_start
                    word_end = float(w.get("end", 0.0)) + seg_start
                    words.append({
                        "word": word_text,
                        "start": round(word_start, 3),
                        "end": round(word_end, 3),
                    })
        
        segment_data = {
            "start": seg_start,
            "end": seg_end,
            "text": seg_text,
        }
        
        # Only include words array if we have word-level data
        if words:
            segment_data["words"] = words
        
        segments.append(segment_data)

    full_text = " ".join([s["text"] for s in segments if s["text"]]).strip()
    return {
        "language": result.get("language") or "unknown",
        "segments": segments,
        "text": full_text,
    }


def diarize_with_pyannote(wav_path, min_speakers=None, max_speakers=None):
    """
    Diarize audio using pyannote.audio pipeline.
    
    Args:
        wav_path: Path to audio file
        min_speakers: Minimum number of speakers (optional, improves accuracy if known)
        max_speakers: Maximum number of speakers (optional, improves accuracy if known)
    
    Returns:
        List of segments with speaker, start, end
    """
    print("3) Diarizing (pyannote)...")

    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=_pick_token()
    )
    
    # Configure pipeline parameters for better accuracy
    # These parameters can be tuned based on audio quality and number of speakers
    pipeline_params = {}
    
    # If speaker count is known, provide hints to improve accuracy
    if min_speakers is not None and max_speakers is not None and min_speakers == max_speakers:
        pipeline_params["num_speakers"] = min_speakers
        print(f"   Using fixed speaker count: {min_speakers}")
    elif min_speakers is not None or max_speakers is not None:
        if min_speakers is not None:
            pipeline_params["min_speakers"] = min_speakers
        if max_speakers is not None:
            pipeline_params["max_speakers"] = max_speakers
        print(f"   Using speaker range: min={min_speakers}, max={max_speakers}")
    
    # Run diarization with parameters
    if pipeline_params:
        diarization = pipeline(str(wav_path), **pipeline_params)
    else:
        diarization = pipeline(str(wav_path))

    segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append({
            "speaker": speaker,
            "start": round(turn.start, 2),
            "end": round(turn.end, 2),
        })
    
    print(f"   Detected {len(set(s['speaker'] for s in segments))} unique speakers")
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

        utterance_data = {
            "start": s,
            "end": e,
            "speaker": speaker,
            "text": seg.get("text", "")
        }
        
        # Preserve word-level timestamps if available
        if "words" in seg and isinstance(seg["words"], list):
            utterance_data["words"] = seg["words"]
        
        utterances.append(utterance_data)

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

    # Get speaker count hints from config if available
    min_speakers = cfg.get("min_speakers")
    max_speakers = cfg.get("max_speakers")
    speakers_expected = cfg.get("speakers_expected")
    
    # If speakers_expected is set, use it for both min and max
    if speakers_expected is not None and isinstance(speakers_expected, int) and speakers_expected > 0:
        min_speakers = speakers_expected
        max_speakers = speakers_expected
    
    diar_segments = diarize_with_pyannote(wav_path, min_speakers=min_speakers, max_speakers=max_speakers)
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
