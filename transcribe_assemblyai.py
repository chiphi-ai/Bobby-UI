# transcribe_assemblyai.py
# Usage:
#   python -u transcribe_assemblyai.py input\Square.m4a --speakers 4
#   python -u transcribe_assemblyai.py input\thehagover.mp4 --speakers 5
#   python -u transcribe_assemblyai.py input\meeting.mp4   (no speakers_expected)
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

    # AssemblyAI includes "utterances" when speaker_labels is enabled
    utterances = full_json.get("utterances") or []

    # Normalize to the shape you already used: start/end/speaker/text
    cleaned = []
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

    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key or api_key == "your-api-key-here":
        # Debug output to help diagnose
        print(f"\nDEBUG: ASSEMBLYAI_API_KEY in os.environ: {bool(os.environ.get('ASSEMBLYAI_API_KEY'))}")
        if os.environ.get('ASSEMBLYAI_API_KEY'):
            print(f"DEBUG: API key value (first 20 chars): {os.environ.get('ASSEMBLYAI_API_KEY', '')[:20]}...")
        print(f"DEBUG: Current working directory: {os.getcwd()}")
        print(f"DEBUG: Script directory: {Path(__file__).parent}")
        print(f"DEBUG: .env file path: {Path(__file__).parent / '.env'}")
        print(f"DEBUG: .env file exists: {(Path(__file__).parent / '.env').exists()}")
        die("Missing ASSEMBLYAI_API_KEY. Set it in .env file (ASSEMBLYAI_API_KEY=your-key) or as env var in PowerShell.")
    
    # Debug: show we got the key (first 10 chars only for security)
    print(f"DEBUG: Using API key (first 10 chars): {api_key[:10]}...")

    input_path = Path(args.input_file)
    if not input_path.exists():
        # Try inside ./input if they passed only filename
        candidate = Path("input") / args.input_file
        if candidate.exists():
            input_path = candidate
        else:
            die(f"File not found: {args.input_file}")

    headers = {"authorization": api_key}

    # Load custom vocabulary (optional - won't break if file doesn't exist)
    custom_vocab = load_custom_vocabulary()

    print("Uploading and transcribing with AssemblyAI (dynamic speakers)...")
    try:
        wav_path = to_wav_16k_mono(input_path, enhance_audio=args.enhance_audio)
    except TypeError:
        print("WARN: to_wav_16k_mono does not accept enhance_audio; running without enhancement.")
        wav_path = to_wav_16k_mono(input_path)
    upload_url = upload_audio(wav_path, headers=headers)
    tid = submit_transcript(upload_url, headers=headers, speakers_expected=args.speakers, speech_threshold=args.speech_threshold, custom_vocab=custom_vocab)
    result = poll_transcript(tid, headers=headers)

    save_outputs(base_stem=input_path.stem, full_json=result)


if __name__ == "__main__":
    main()
