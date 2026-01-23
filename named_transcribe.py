# named_transcribe.py
#
# One-shot pipeline:
#   - Convert input audio/video -> output/<stem>_16k.wav
#   - AssemblyAI transcription w/ diarized utterances
#   - Enroll speaker embeddings from enroll/ (any audio/video formats)
#   - Slice meeting wav per utterance -> embed -> match to enroll
#   - Output: output/<stem>_named_script.txt (+ .json + utterances json)
#
# Usage:
#   python named_transcribe.py input\normal.m4a enroll --speakers 4
#   python named_transcribe.py input\talk.m4a   enroll --speakers 4 --min-score 0.55
#
# Requirements:
#   pip install requests speechbrain torch torchaudio soundfile numpy python-dotenv
#   ffmpeg in PATH
#   set env var ASSEMBLYAI_API_KEY or create .env file with ASSEMBLYAI_API_KEY=your-key

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import requests
import torch
import torchaudio
from speechbrain.inference.speaker import EncoderClassifier

# Try to load from .env file if python-dotenv is available
# override=True ensures .env file values take precedence over existing env vars
try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass  # python-dotenv not installed, will use environment variables only

API_BASE = "https://api.assemblyai.com/v2"


# -----------------------------
# Utilities
# -----------------------------
def die(msg: str, code: int = 1) -> None:
    print(f"\nERROR: {msg}\n", file=sys.stderr)
    raise SystemExit(code)


def run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        print(p.stdout)
        die(f"Command failed: {' '.join(cmd)}")


def ensure_dirs():
    Path("output").mkdir(parents=True, exist_ok=True)


def to_wav_16k_mono(input_path: Path, out_wav: Path) -> Path:
    ensure_dirs()
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    print("1) Converting input to WAV (16k mono)...")
    run(cmd)
    return out_wav


def slice_wav(in_wav: Path, start_s: float, end_s: float, out_wav: Path) -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.01, end_s - start_s)
    run([
        "ffmpeg", "-y",
        "-ss", f"{start_s:.3f}",
        "-t", f"{dur:.3f}",
        "-i", str(in_wav),
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ])


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = a / (np.linalg.norm(a) + 1e-9)
    b = b / (np.linalg.norm(b) + 1e-9)
    return float(np.dot(a, b))


def embed(classifier: EncoderClassifier, wav_path: Path) -> np.ndarray:
    """Load audio -> mono -> resample 16k -> encode_batch -> 1D numpy embedding."""
    wav_path = str(wav_path)
    waveform, sr = torchaudio.load(wav_path)  # [channels, time]

    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    target_sr = 16000
    if sr != target_sr:
        waveform = torchaudio.functional.resample(waveform, sr, target_sr)

    # Most SpeechBrain versions accept [batch, time] or [batch, channels, time].
    # We'll use [batch, time] (mono => [1, time]).
    if waveform.dim() == 2:
        batch_wav = waveform  # [1, time]
    else:
        batch_wav = waveform.squeeze(0)

    with torch.no_grad():
        emb = classifier.encode_batch(batch_wav)

    return emb.squeeze().cpu().numpy()


# -----------------------------
# AssemblyAI
# -----------------------------
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


def submit_transcript(upload_url: str, headers: dict, speakers_expected: int | None, speech_threshold: float | None, custom_vocab: list[str] = None):
    print("3) Submitting transcription job...")
    payload = {
        "audio_url": upload_url,
        "punctuate": True,
        "format_text": True,
        "speaker_labels": True,
    }
    if speakers_expected is not None:
        payload["speakers_expected"] = int(speakers_expected)
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


def clean_utterances(full_json: dict) -> list[dict]:
    utterances = full_json.get("utterances") or []
    cleaned = []
    for u in utterances:
        cleaned.append({
            "start": (u.get("start") or 0) / 1000.0,
            "end": (u.get("end") or 0) / 1000.0,
            "speaker": u.get("speaker") or "Unknown",  # AssemblyAI label (A/B/C...)
            "text": (u.get("text") or "").strip(),
        })
    return cleaned


# -----------------------------
# Matching + smoothing
# -----------------------------
def merge_consecutive(rows: list[dict]) -> list[dict]:
    out = []
    last = None
    for r in rows:
        if not r["text"].strip():
            continue
        if last and last["speaker_name"] == r["speaker_name"]:
            last["text"] = (last["text"] + " " + r["text"]).strip()
            last["end"] = r["end"]
            # keep best/avg scores
            last["score"] = float(max(last["score"], r["score"]))
            last["gap"] = float(min(last["gap"], r.get("gap", last["gap"])))
        else:
            last = dict(r)
            out.append(last)
    return out


def choose_speaker_with_smoothing(scores: dict[str, float], prev: str | None, switch_penalty: float) -> tuple[str, float, float]:
    """
    scores: name -> cosine score
    prev: previous chosen name
    switch_penalty: subtract this from all non-prev names to reduce jitter
    Returns: (best_name, best_score, top2_gap)
    """
    items = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not items:
        return ("Unknown", -1e9, 0.0)

    # Apply penalty to encourage staying on same speaker
    adjusted = []
    for name, sc in items:
        adj = sc
        if prev is not None and name != prev:
            adj = sc - switch_penalty
        adjusted.append((name, sc, adj))

    adjusted.sort(key=lambda t: t[2], reverse=True)
    best_name, best_raw, best_adj = adjusted[0]

    # compute gap between top-1 and top-2 raw
    top1 = items[0][1]
    top2 = items[1][1] if len(items) > 1 else items[0][1]
    gap = float(top1 - top2)

    return best_name, float(best_raw), gap


# -----------------------------
# Main
# -----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", help=r"Path to audio/video file (e.g., input\normal.m4a)")
    parser.add_argument("enroll_dir", help=r"Folder with enrolled speakers (e.g., enroll)")
    parser.add_argument("--speakers", type=int, default=None, help="Expected number of speakers for AssemblyAI (e.g., 4).")
    parser.add_argument("--speech-threshold", type=float, default=None, help="0.0-1.0; try 0.6-0.8 to ignore noise/music.")
    parser.add_argument("--min-seg-seconds", type=float, default=0.8, help="Skip segments shorter than this.")
    parser.add_argument("--min-score", type=float, default=0.50, help="If best cosine score < this, label as Unknown.")
    parser.add_argument("--min-gap", type=float, default=0.03, help="If (top1-top2) gap < this, label as Unknown.")
    parser.add_argument("--switch-penalty", type=float, default=0.02, help="Penalty applied when switching speakers to reduce jitter.")
    args = parser.parse_args()

    backend = os.getenv("TRANSCRIPTION_BACKEND", "whisper").strip().lower()
    headers = None
    if backend in {"assemblyai", "aai"}:
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key or api_key == "your-api-key-here":
            die(
                "TRANSCRIPTION_BACKEND=assemblyai but ASSEMBLYAI_API_KEY is missing.\n"
                "Set it in .env file (ASSEMBLYAI_API_KEY=your-key) or switch to local backend:\n"
                "  TRANSCRIPTION_BACKEND=whisper"
            )
    headers = {"authorization": api_key}

    input_path = Path(args.input_file)
    if not input_path.exists():
        candidate = Path("input") / args.input_file
        if candidate.exists():
            input_path = candidate
        else:
            die(f"File not found: {args.input_file}")

    enroll_dir = Path(args.enroll_dir)
    if not enroll_dir.exists():
        die(f"Enroll folder not found: {enroll_dir}")

    ensure_dirs()
    stem = input_path.stem
    meeting_wav = Path("output") / f"{stem}_16k.wav"

    # Convert meeting audio
    to_wav_16k_mono(input_path, meeting_wav)
    print(f"   meeting wav: {meeting_wav}")

    # Load custom vocabulary (optional - won't break if file doesn't exist)
    # Try to get user email from environment (set by web_app.py pipeline)
    user_email = os.environ.get("VOCABULARY_USER_EMAIL", "").strip() or None
    custom_vocab = load_custom_vocabulary(user_email=user_email)

    out_full = Path("output") / f"{stem}_aai.json"
    out_utter = Path("output") / f"{stem}_utterances.json"

    if backend in {"assemblyai", "aai"}:
        # Transcribe with diarization via AssemblyAI (legacy fallback)
        upload_url = upload_audio(meeting_wav, headers=headers)
        tid = submit_transcript(
            upload_url,
            headers=headers,
            speakers_expected=args.speakers,
            speech_threshold=args.speech_threshold,
            custom_vocab=custom_vocab,
        )
        full = poll_transcript(tid, headers=headers)
    out_full.write_text(json.dumps(full, indent=2, ensure_ascii=False), encoding="utf-8")
    utterances = clean_utterances(full)
    out_utter.write_text(json.dumps(utterances, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"5) Saved:\n   {out_full}\n   {out_utter}")
    else:
        # Local backend: Whisper transcription + pyannote diarization (preferred)
        from transcribe_assemblyai import (
            transcribe_with_whisper,
            diarize_with_pyannote,
            align_transcript_and_diarization,
        )
        transcript = transcribe_with_whisper(meeting_wav, custom_vocab=custom_vocab)
        diar_segments = diarize_with_pyannote(meeting_wav, speakers_expected=args.speakers)
        utterances = align_transcript_and_diarization(transcript, diar_segments)

        full = {
            "backend": "whisper+pyannote",
            "input": str(input_path),
            "wav": str(meeting_wav),
            "transcript": transcript,
            "diarization": diar_segments,
            "utterances": utterances,
        }
        out_full.write_text(json.dumps(full, indent=2, ensure_ascii=False), encoding="utf-8")
        out_utter.write_text(json.dumps(utterances, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"5) Saved:\n   {out_full}\n   {out_utter}")

    # Load speaker embedding model
    print("6) Loading speaker embedding model (SpeechBrain ECAPA)...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    classifier = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        run_opts={"device": device},
    )

    # Build enrollment embeddings
    print("7) Building enrollment embeddings...")
    tmp_enroll = Path("output") / "_enroll_wavs"
    tmp_enroll.mkdir(parents=True, exist_ok=True)

    enroll_embs: dict[str, np.ndarray] = {}
    supported = {".wav", ".mp3", ".m4a", ".mp4", ".mov", ".aac", ".flac", ".ogg"}

    for p in sorted(enroll_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in supported:
            name = p.stem.lower()
            wav = tmp_enroll / f"{name}.wav"
            to_wav_16k_mono(p, wav)
            enroll_embs[name] = embed(classifier, wav)
            print(f"   enrolled: {name} ({p.name})")

    if not enroll_embs:
        die("No enrollment audio files found in enroll folder.")

    # Match each utterance segment to enrolled voices
    print("8) Matching diarized utterances to enrolled speakers...")
    tmp_segs = Path("output") / "_seg_wavs"
    tmp_segs.mkdir(parents=True, exist_ok=True)

    # Track unknown speakers: map diarization speaker ID -> Speaker N
    unknown_speaker_map = {}  # diarization_speaker_id -> "Speaker N"
    unknown_counter = 1  # Next unknown speaker number

    labeled = []
    prev_name: str | None = None

    for i, u in enumerate(utterances):
        start = float(u["start"])
        end = float(u["end"])
        txt = (u.get("text") or "").strip()
        if not txt:
            continue

        if end - start < args.min_seg_seconds:
            continue

        seg_wav = tmp_segs / f"seg_{i:05d}.wav"
        slice_wav(meeting_wav, start, end, seg_wav)

        seg_emb = embed(classifier, seg_wav)

        scores = {name: cosine(seg_emb, e) for name, e in enroll_embs.items()}
        best_name, best_score, gap = choose_speaker_with_smoothing(
            scores, prev=prev_name, switch_penalty=args.switch_penalty
        )

        # Get diarization speaker ID for tracking unknowns
        diarization_speaker = u.get("speaker", f"SPEAKER_{i}")

        # Confidence gating
        if best_score < args.min_score or gap < args.min_gap:
            # Low confidence or no match -> assign to unknown speaker
            # Use diarization speaker ID to track consistency
            if diarization_speaker not in unknown_speaker_map:
                # New unknown speaker - assign next number
                unknown_speaker_map[diarization_speaker] = f"Speaker {unknown_counter}"
                unknown_counter += 1
            
            speaker_name = unknown_speaker_map[diarization_speaker]
            prev_name = None  # Don't use unknown for smoothing
        else:
            speaker_name = best_name
            prev_name = speaker_name  # only advance prev when confident

        # Normalize speaker name: remove (2), (3) etc. if present
        normalized_name = speaker_name
        is_unknown = speaker_name.startswith("Speaker ") and len(speaker_name) > 8 and speaker_name[8:].split()[0].isdigit()
        if normalized_name and not is_unknown and normalized_name != "Unknown":
            normalized_name = re.sub(r"\(\d+\)", "", normalized_name).strip()
        elif not is_unknown and normalized_name == "Unknown":
            # Legacy "Unknown" -> convert to "Speaker 1" for consistency
            if diarization_speaker not in unknown_speaker_map:
                unknown_speaker_map[diarization_speaker] = "Speaker 1"
                unknown_counter = max(unknown_counter, 2)  # Ensure next is 2+
            normalized_name = unknown_speaker_map[diarization_speaker]
            is_unknown = True
        
        labeled.append({
            "start": start,
            "end": end,
            "speaker_name": normalized_name,
            "score": float(best_score),
            "gap": float(gap),
            "aai_speaker": u.get("speaker"),
            "text": txt,
            "is_unknown": is_unknown,
            "diarization_speaker": diarization_speaker
        })

    # Merge consecutive lines
    labeled = merge_consecutive(labeled)

    # Write outputs
    out_txt = Path("output") / f"{stem}_named_script.txt"
    out_json = Path("output") / f"{stem}_named_script.json"

    # Format speaker names for display (remove (2), (3) etc. and format properly)
    lines = []
    for r in labeled:
        speaker_name = r['speaker_name']
        is_unknown = r.get('is_unknown', False)
        
        # Handle unknown speakers (keep as "Speaker N")
        if is_unknown or (speaker_name.startswith("Speaker ") and len(speaker_name) > 8 and speaker_name[8:].split()[0].isdigit()):
            # Keep unknown speaker labels as-is
            formatted_name = speaker_name
        else:
            # Remove any (2), (3) etc. patterns
            speaker_name = re.sub(r"\(\d+\)", "", speaker_name)
            # Convert "bobby,jones" to "Bobby Jones" for display
            if "," in speaker_name and speaker_name != "Unknown":
                parts = speaker_name.split(",")
                if len(parts) == 2:
                    first = parts[0].strip().capitalize()
                    last = parts[1].strip().capitalize()
                    formatted_name = f"{first} {last}"
            elif speaker_name != "Unknown":
                formatted_name = speaker_name.strip().capitalize()
            else:
                # Legacy "Unknown" -> convert to "Speaker 1"
                formatted_name = "Speaker 1"
        
        lines.append(f"{formatted_name}: {r['text']}")
    out_txt.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    
    # Print summary of unknown speakers
    unknown_speakers_found = [r['speaker_name'] for r in labeled if r.get('is_unknown', False) or (r['speaker_name'].startswith("Speaker ") and len(r['speaker_name']) > 8 and r['speaker_name'][8:].split()[0].isdigit())]
    if unknown_speakers_found:
        unique_unknowns = sorted(set(unknown_speakers_found))
        print(f"Identified {len(unique_unknowns)} unidentified speaker(s): {', '.join(unique_unknowns)}")
    out_json.write_text(json.dumps(labeled, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nDONE. Wrote:\n  {out_txt}\n  {out_json}\n  {out_utter}\n")


if __name__ == "__main__":
    main()