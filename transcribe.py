# transcribe_assemblyai.py
# Usage:
#   python -u transcribe_assemblyai.py input\Square.m4a --speakers 4
#   python -u transcribe_assemblyai.py input\thehagover.mp4 --speakers 5
#   python -u transcribe_assemblyai.py input\meeting.mp4   (no speakers_expected)
#
# NOTE (2026-01):
#   This script now supports a local backend (Whisper + pyannote) while preserving
#   the exact same output files used by the rest of the app:
#     - output/<stem>_16k.wav
#     - output/<stem>_utterances.json   [{start,end,speaker,text}, ...]
#     - output/<stem>_script.txt
#     - output/<stem>_aai.json          (kept for compatibility; now contains backend metadata)
#   Backend selection:
#     TRANSCRIPTION_BACKEND=whisper  (default)  -> Whisper transcription + pyannote diarization
#     TRANSCRIPTION_BACKEND=assemblyai         -> Original AssemblyAI flow
#
# Requires:
#   - ffmpeg in PATH
#   - pip install requests python-dotenv
#   - set env var ASSEMBLYAI_API_KEY or create .env file with ASSEMBLYAI_API_KEY=your-key

import argparse
import json
import os
import sys
import time
import subprocess
from pathlib import Path
import tempfile

import requests

# Try to load from .env file if python-dotenv is available
# override=True ensures .env file values take precedence over existing env vars
# Use utf-8-sig to handle BOM if present in .env file
try:
    from dotenv import load_dotenv
    from pathlib import Path
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        # Read directly to handle BOM, then use load_dotenv
        with open(env_file, 'r', encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if line.startswith('ASSEMBLYAI_API_KEY='):
                    api_key = line.split('=', 1)[1].strip()
                    if api_key and api_key != "your-api-key-here":
                        os.environ["ASSEMBLYAI_API_KEY"] = api_key
                        break
    load_dotenv(env_file, override=True)
except ImportError:
    pass  # python-dotenv not installed, will use environment variables only
except Exception:
    pass  # If file read fails, fall back to load_dotenv only


API_BASE = "https://api.assemblyai.com/v2"


def load_custom_vocabulary(vocab_path: Path = None, user_email: str = None) -> list[str]:
    """
    Load custom vocabulary for word boosting in AssemblyAI.
    
    Priority:
    1. User-specific vocabulary from database (if user_email provided)
    2. Fallback to custom_vocabulary.txt file (backward compatible)
    
    Args:
        vocab_path: Optional path to vocabulary file (for backward compatibility)
        user_email: Optional user email to load user-specific vocabulary
    
    Returns:
        List of vocabulary terms
    """
    words = []
    
    # Try to load user-specific vocabulary from database first
    if user_email:
        try:
            # Import here to avoid circular imports
            import sys
            import os
            # Add parent directory to path to import web_app functions
            parent_dir = Path(__file__).parent
            if str(parent_dir) not in sys.path:
                sys.path.insert(0, str(parent_dir))
            
            # Try to import vocabulary functions
            try:
                from web_app import get_user_custom_vocabulary
                user_words = get_user_custom_vocabulary(user_email)
                if user_words:
                    words.extend(user_words)
                    print(f"Loaded {len(user_words)} custom vocabulary terms for user {user_email}")
            except ImportError:
                # web_app not available (standalone script), skip user vocab
                pass
        except Exception as e:
            print(f"Warning: Could not load user vocabulary: {e}")
    
    # Fallback to file-based vocabulary (backward compatible)
    if vocab_path is None:
        vocab_path = Path(__file__).parent / "custom_vocabulary.txt"
    
    if vocab_path.exists():
        try:
            with open(vocab_path, "r", encoding="utf-8") as f:
                for line in f:
                    word = line.strip()
                    # Skip empty lines and comments
                    if word and not word.startswith("#"):
                        if word not in words:  # Avoid duplicates
                            words.append(word)
            if words and not user_email:
                print(f"Loaded {len(words)} custom vocabulary words from {vocab_path.name}")
        except Exception as e:
            print(f"Warning: Could not load custom vocabulary file: {e}")
    
    return words


def die(msg: str, code: int = 1) -> None:
    print(f"\nERROR: {msg}\n", file=sys.stderr)
    raise SystemExit(code)


def run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        print(p.stdout)
        die(f"Command failed: {' '.join(cmd)}")
    # optional: print ffmpeg output if you want
    # print(p.stdout)


def ensure_dirs():
    Path("output").mkdir(parents=True, exist_ok=True)


def to_wav_16k_mono(input_path: Path, enhance_audio: bool = False, **kwargs) -> Path:
    ensure_dirs()
    out_wav = Path("output") / f"{input_path.stem}_16k.wav"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vn",
    ]
    if enhance_audio:
        # Basic denoise + loudness normalization for noisy recordings
        cmd += ["-af", "afftdn,loudnorm=I=-16:TP=-1.5:LRA=11"]
    cmd += [
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    print("1) Converting to WAV (16k mono)...")
    run(cmd)
    print(f"   wrote: {out_wav}")
    return out_wav


def ffprobe_duration_seconds(path: Path) -> float:
    """Get duration in seconds via ffprobe (best-effort)."""
    try:
        p = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if p.returncode == 0 and p.stdout.strip():
            return float(p.stdout.strip())
    except Exception:
        pass
    return 0.0


def _fmt_hms(seconds: float) -> str:
    s = int(round(max(0.0, float(seconds))))
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{ss:02d}"
    return f"{m:d}:{ss:02d}"

def _pick_token() -> str | None:
    # pyannote uses HuggingFace auth tokens for model downloads (speaker-diarization-3.1 is gated)
    return (
        os.environ.get("HF_TOKEN", "").strip()
        or os.environ.get("HUGGINGFACE_TOKEN", "").strip()
        or None
    )

def _midpoint(a: float, b: float) -> float:
    return (a + b) / 2.0

def transcribe_with_whisper(wav_path: Path, custom_vocab: list[str] | None = None) -> dict:
    """
    Returns a dict with:
      - text: full transcript text
      - segments: [{start,end,text}, ...] (seconds)
      - language (if available)
    """
    try:
        import torch
    except Exception:
        torch = None

    try:
        import whisper  # openai-whisper
    except Exception as e:
        die(
            "Whisper backend selected but dependency is missing.\n"
            "Install: pip install -r requirements.txt\n"
            f"Import error: {e}"
        )

    model_name = os.getenv("WHISPER_MODEL", "small").strip() or "small"
    language = os.getenv("WHISPER_LANGUAGE", "").strip() or None

    # Device selection:
    # - If WHISPER_DEVICE is set, honor it.
    # - Otherwise: prefer CUDA (if available), else CPU.
    #
    # Note: MPS (Apple Silicon GPU) can be flaky for Whisper depending on torch/whisper versions.
    # We only use MPS when explicitly requested, and we will automatically fall back to CPU if MPS fails.
    requested_device = os.getenv("WHISPER_DEVICE", "").strip().lower()
    device = requested_device or "cpu"
    if not requested_device and torch is not None:
        if getattr(torch, "cuda", None) and torch.cuda.is_available():
            device = "cuda"

    # If user explicitly requests MPS, enable fallback to CPU for unsupported ops.
    if device == "mps":
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

    chunk_seconds_env = os.getenv("WHISPER_CHUNK_SECONDS", "").strip()
    try:
        chunk_seconds = int(chunk_seconds_env) if chunk_seconds_env else 0
    except Exception:
        chunk_seconds = 0

    print(f"2) Transcribing (Whisper: model={model_name}, device={device})...")
    try:
        model = whisper.load_model(model_name, device=device)
    except NotImplementedError as e:
        # Common on macOS when some ops are not implemented for MPS/SparseMPS.
        if device == "mps":
            print(f"⚠️  Whisper failed on MPS ({e}). Falling back to CPU...")
            device = "cpu"
            model = whisper.load_model(model_name, device=device)
        else:
            raise

    initial_prompt = None
    if custom_vocab:
        # Keep prompt modest (Whisper prompt is not a strict vocab/boosting mechanism)
        prompt_words = ", ".join(custom_vocab[:80])
        initial_prompt = f"Important terms: {prompt_words}."

    total_seconds = ffprobe_duration_seconds(wav_path)

    # If chunking is enabled (WHISPER_CHUNK_SECONDS>0), transcribe sequential chunks
    # so we can print accurate "percent transcribed" progress.
    if chunk_seconds and total_seconds and total_seconds > max(30.0, float(chunk_seconds) * 1.25):
        segments_all: list[dict] = []
        with tempfile.TemporaryDirectory(prefix=f"whisper_chunks_{wav_path.stem}_") as td:
            tmp_dir = Path(td)
            chunk_pattern = tmp_dir / "chunk_%05d.wav"

            # Segment the wav into fixed-size chunks (fast copy). If it fails, fall back to re-encode.
            seg_cmd = [
                "ffmpeg", "-y",
                "-i", str(wav_path),
                "-f", "segment",
                "-segment_time", str(int(chunk_seconds)),
                "-reset_timestamps", "1",
                "-c", "copy",
                str(chunk_pattern),
            ]
            p = subprocess.run(seg_cmd, capture_output=True, text=True)
            if p.returncode != 0:
                seg_cmd = [
                    "ffmpeg", "-y",
                    "-i", str(wav_path),
                    "-f", "segment",
                    "-segment_time", str(int(chunk_seconds)),
                    "-reset_timestamps", "1",
                    "-ac", "1",
                    "-ar", "16000",
                    "-c:a", "pcm_s16le",
                    str(chunk_pattern),
                ]
                run(seg_cmd)

            chunk_files = sorted(tmp_dir.glob("chunk_*.wav"))
            if not chunk_files:
                die("Failed to create transcription chunks.")

            total_chunks = len(chunk_files)
            prev_tail = ""
            for idx, chunk_path in enumerate(chunk_files):
                offset = float(idx) * float(chunk_seconds)
                prompt = initial_prompt
                if prev_tail:
                    prompt = (prompt + " " if prompt else "") + f"Previous context: {prev_tail}"

                chunk_result = model.transcribe(
                    str(chunk_path),
                    language=language,
                    fp16=(device == "cuda"),
                    initial_prompt=prompt,
                    verbose=False,
                )

                chunk_segments = []
                for s in (chunk_result.get("segments") or []):
                    chunk_segments.append({
                        "start": float(s.get("start", 0.0)) + offset,
                        "end": float(s.get("end", 0.0)) + offset,
                        "text": (s.get("text") or "").strip(),
                    })
                segments_all.extend([s for s in chunk_segments if s.get("text")])

                # Update a small rolling tail for better continuity between chunks
                try:
                    tail_words = " ".join([s["text"] for s in chunk_segments if s.get("text")]).split()[-30:]
                    prev_tail = " ".join(tail_words)
                except Exception:
                    prev_tail = ""

                done_seconds = min(float(total_seconds), float(idx + 1) * float(chunk_seconds))
                pct = int(round((done_seconds / float(total_seconds)) * 100.0))
                print(
                    f"TRANSCRIBE_PROGRESS percent={pct} done={done_seconds:.1f} total={float(total_seconds):.1f} "
                    f"human={_fmt_hms(done_seconds)}/{_fmt_hms(total_seconds)} chunks={idx+1}/{total_chunks}"
                )
                print(f"   Transcribed {_fmt_hms(done_seconds)} / {_fmt_hms(total_seconds)} ({pct}%)")

        full_text = " ".join([s["text"] for s in segments_all if s.get("text")]).strip()
        return {
            "backend": "whisper",
            "model": model_name,
            "device": device,
            "language": language,
            "text": full_text,
            "segments": segments_all,
        }

    # Default: single-shot transcription
    result = model.transcribe(
        str(wav_path),
        language=language,
        fp16=(device == "cuda"),
        initial_prompt=initial_prompt,
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
        "backend": "whisper",
        "model": model_name,
        "device": device,
        "language": result.get("language"),
        "text": full_text,
        "segments": segments,
    }

def diarize_with_pyannote(wav_path: Path, speakers_expected: int | None = None) -> list[dict]:
    print("3) Diarizing (pyannote)...")
    
    # PyTorch 2.6+ compatibility: allow TorchVersion in safe globals for model loading
    try:
        import torch
        import torch.torch_version
        torch.serialization.add_safe_globals([torch.torch_version.TorchVersion])
    except Exception:
        pass  # Older PyTorch versions don't need this
    
    token = _pick_token()
    if not token:
        die(
            "Missing HuggingFace token for pyannote.\n"
            "Set HF_TOKEN (or HUGGINGFACE_TOKEN) in your environment or .env.\n"
            "Then accept the model license for `pyannote/speaker-diarization-3.1` in HuggingFace."
        )
    # Compatibility shim:
    # Some pyannote.audio versions call `huggingface_hub.hf_hub_download(..., use_auth_token=...)`,
    # but newer huggingface_hub versions renamed that parameter to `token`.
    # Patch in a wrapper so we don't have to pin fragile dependency versions.
    try:
        import huggingface_hub
        from huggingface_hub import hf_hub_download as _orig_hf_hub_download

        def _hf_hub_download_compat(*args, **kwargs):
            if "use_auth_token" in kwargs and "token" not in kwargs:
                kwargs["token"] = kwargs.pop("use_auth_token")
            else:
                kwargs.pop("use_auth_token", None)
            return _orig_hf_hub_download(*args, **kwargs)

        huggingface_hub.hf_hub_download = _hf_hub_download_compat  # type: ignore[attr-defined]
    except Exception:
        # If anything goes wrong, let pyannote/hub raise a normal error.
        pass
    try:
        from pyannote.audio import Pipeline
    except Exception as e:
        die(
            "pyannote backend selected but dependency is missing.\n"
            "Install: pip install -r requirements.txt\n"
            f"Import error: {e}"
        )

    # Try different parameter names for different pyannote versions
    try:
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=token,
        )
    except TypeError:
        # Fallback: set token via huggingface_hub login
        try:
            from huggingface_hub import login
            login(token=token, add_to_git_credential=False)
            pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
        except Exception:
            # Last resort: try without token (might work if already logged in)
            pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")

    # pyannote pipelines accept num_speakers / min_speakers / max_speakers (varies by version).
    diarization = None
    if speakers_expected is not None:
        try:
            diarization = pipeline(str(wav_path), num_speakers=int(speakers_expected))
        except TypeError:
            diarization = pipeline(str(wav_path))
    else:
        diarization = pipeline(str(wav_path))

    segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append({
            "speaker": str(speaker),
            "start": float(turn.start),
            "end": float(turn.end),
        })

    # Sort just in case
    segments.sort(key=lambda d: (d["start"], d["end"]))
    return segments

def align_transcript_and_diarization(transcript: dict, diar_segments: list[dict]) -> list[dict]:
    """
    Simple alignment:
      For each transcript segment, assign the diarization speaker whose segment contains the transcript midpoint.
    Output matches the existing utterances schema used downstream.
    """
    diar_i = 0
    diar_n = len(diar_segments)

    utterances = []
    for seg in (transcript.get("segments") or []):
        s = float(seg.get("start", 0.0))
        e = float(seg.get("end", 0.0))
        txt = (seg.get("text") or "").strip()
        if not txt:
            continue

        m = _midpoint(s, e)
        while diar_i < diar_n and float(diar_segments[diar_i].get("end", 0.0)) < m:
            diar_i += 1

        speaker = "Unknown"
        if diar_i < diar_n:
            d = diar_segments[diar_i]
            if float(d.get("start", 0.0)) <= m <= float(d.get("end", 0.0)):
                speaker = d.get("speaker") or "Unknown"

        utterances.append({
            "start": s,
            "end": e,
            "speaker": speaker,
            "text": txt,
        })
    return utterances


def upload_audio(wav_path: Path, headers: dict) -> str:
    print("2) Uploading audio to AssemblyAI...")
    with open(wav_path, "rb") as f:
        r = requests.post(f"{API_BASE}/upload", headers=headers, data=f)
    if r.status_code >= 300:
        die(f"Upload failed ({r.status_code}): {r.text}")
    upload_url = r.json().get("upload_url")
    if not upload_url:
        die("Upload response missing upload_url.")
    return upload_url


def submit_transcript(upload_url: str, headers: dict, speakers_expected: int | None, speech_threshold: float | None, custom_vocab: list[str] = None):
    print("3) Submitting transcription job...")
    payload = {
        "audio_url": upload_url,
        "punctuate": True,
        "format_text": True,
        "speaker_labels": True,  # enables diarization labels
    }

    # If you know the number of speakers, this helps a lot.
    # AssemblyAI API supports speakers_expected in the transcript request body.
    if speakers_expected is not None:
        payload["speakers_expected"] = int(speakers_expected)

    # This can help reduce false positives in noisy/music audio.
    # (Higher = stricter about what counts as speech; try 0.6–0.8 if music is getting transcribed.)
    if speech_threshold is not None:
        payload["speech_threshold"] = float(speech_threshold)
    
    # Custom vocabulary for word boosting (improves recognition of domain-specific terms)
    if custom_vocab:
        payload["word_boost"] = custom_vocab
        print(f"   Using {len(custom_vocab)} custom vocabulary words for word boosting")

    r = requests.post(f"{API_BASE}/transcript", headers=headers, json=payload)
    if r.status_code >= 300:
        die(f"Submit failed ({r.status_code}): {r.text}")

    tid = r.json().get("id")
    if not tid:
        die("Transcript submit response missing id.")
    return tid


def poll_transcript(tid: str, headers: dict, poll_seconds: int = 3, timeout_seconds: int = 60 * 60):
    print(f"4) Polling until complete (id={tid})...")
    start = time.time()
    while True:
        r = requests.get(f"{API_BASE}/transcript/{tid}", headers=headers)
        if r.status_code >= 300:
            die(f"Poll failed ({r.status_code}): {r.text}")
        data = r.json()
        status = data.get("status")

        if status == "completed":
            return data
        if status == "error":
            die(f"AssemblyAI error: {data.get('error')}")

        if time.time() - start > timeout_seconds:
            die("Timed out waiting for transcription.")

        print(f"   status={status} ...")
        time.sleep(poll_seconds)


def save_outputs(base_stem: str, full_json: dict):
    ensure_dirs()

    out_full = Path("output") / f"{base_stem}_aai.json"
    out_utter = Path("output") / f"{base_stem}_utterances.json"
    out_script = Path("output") / f"{base_stem}_script.txt"

    out_full.write_text(json.dumps(full_json, indent=2, ensure_ascii=False), encoding="utf-8")

    backend = (full_json.get("backend") or "").lower()
    cleaned: list[dict] = []

    if backend in {"whisper+pyannote", "whisper_pyannote", "whisper"}:
        # Already in seconds with the desired schema
        cleaned = full_json.get("utterances") or []
    else:
        # AssemblyAI includes "utterances" when speaker_labels is enabled (ms)
        utterances = full_json.get("utterances") or []
        for u in utterances:
            cleaned.append({
                "start": (u.get("start") or 0) / 1000.0,   # ms -> seconds
                "end": (u.get("end") or 0) / 1000.0,
                "speaker": u.get("speaker") or "Unknown",
                "text": (u.get("text") or "").strip(),
            })

    out_utter.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False), encoding="utf-8")

    # Build a script-like TXT:
    # Merge consecutive utterances from same speaker for readability.
    lines = []
    last_speaker = None
    buffer = []

    def flush():
        nonlocal buffer, last_speaker
        if not buffer:
            return
        text = " ".join(buffer).strip()
        if text:
            lines.append(f"{last_speaker}: {text}")
        buffer = []

    for row in cleaned:
        spk = row["speaker"]
        txt = row["text"]
        if not txt:
            continue
        if last_speaker is None:
            last_speaker = spk
        if spk != last_speaker:
            flush()
            last_speaker = spk
        buffer.append(txt)

    flush()
    out_script.write_text("\n\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nWrote:\n  {out_full}\n  {out_utter}\n  {out_script}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", help="Path to audio/video file (e.g., input\\Square.m4a)")
    parser.add_argument("--speakers", type=int, default=None, help="Expected number of speakers (e.g., 4). Omit to auto-detect.")
    parser.add_argument("--speech-threshold", type=float, default=None, help="0.0-1.0. Try 0.6-0.8 to ignore music/noise.")
    parser.add_argument("--enhance-audio", action="store_true", help="Apply audio enhancement (denoising + normalization). Use for noisy recordings.")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        # Try inside ./input if they passed only filename
        candidate = Path("input") / args.input_file
        if candidate.exists():
            input_path = candidate
        else:
            die(f"File not found: {args.input_file}")

    backend = os.getenv("TRANSCRIPTION_BACKEND", "whisper").strip().lower()

    # Convert to wav first (both backends need it)
    try:
        wav_path = to_wav_16k_mono(input_path, enhance_audio=args.enhance_audio)
    except TypeError:
        print("WARN: to_wav_16k_mono does not accept enhance_audio; running without enhancement.")
        wav_path = to_wav_16k_mono(input_path)

    # Load custom vocabulary (optional)
    user_email = os.environ.get("VOCABULARY_USER_EMAIL", "").strip() or None
    custom_vocab = load_custom_vocabulary(user_email=user_email)

    if backend in {"assemblyai", "aai"}:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key or api_key == "your-api-key-here":
            die(
                "TRANSCRIPTION_BACKEND=assemblyai but ASSEMBLYAI_API_KEY is missing.\n"
                "Set it in .env (ASSEMBLYAI_API_KEY=...) or switch to local backend:\n"
                "  TRANSCRIPTION_BACKEND=whisper"
            )
        headers = {"authorization": api_key}

        print("Uploading and transcribing with AssemblyAI (speaker labels enabled)...")
        upload_url = upload_audio(wav_path, headers=headers)
        tid = submit_transcript(
            upload_url,
            headers=headers,
            speakers_expected=args.speakers,
            speech_threshold=args.speech_threshold,
            custom_vocab=custom_vocab,
        )
        result = poll_transcript(tid, headers=headers)
        save_outputs(base_stem=input_path.stem, full_json=result)
        return

    # Default: local Whisper + pyannote, but keep the same output contract.
    transcript = transcribe_with_whisper(wav_path, custom_vocab=custom_vocab)
    diar_segments = diarize_with_pyannote(wav_path, speakers_expected=args.speakers)
    utterances = align_transcript_and_diarization(transcript, diar_segments)

    full = {
        "backend": "whisper+pyannote",
        "input": str(input_path),
        "wav": str(wav_path),
        "transcript": transcript,
        "diarization": diar_segments,
        "utterances": utterances,
    }
    save_outputs(base_stem=input_path.stem, full_json=full)


if __name__ == "__main__":
    main()
