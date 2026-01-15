# identify_speakers.py
# Usage:
#   python identify_speakers.py output/Square_utterances.json enroll output/Square_named_script.txt
#
# Requirements:
#   pip install speechbrain torch torchaudio soundfile numpy
#   ffmpeg in PATH (for converting + slicing)

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch
try:
    # Newer SpeechBrain (some installs)
    from speechbrain.inference.speaker import EncoderClassifier  # type: ignore
except Exception:
    # SpeechBrain 0.5.x (this repo's pinned version)
    from speechbrain.pretrained import EncoderClassifier  # type: ignore


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        print(p.stdout)
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def to_wav_16k_mono(in_path: Path, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out_path),
    ])


def slice_wav(in_wav: Path, start_s: float, end_s: float, out_wav: Path):
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

import torch
import torchaudio

def embed(classifier, wav_path):
    """
    Returns a 1D numpy embedding for an audio file path.
    Loads audio -> converts to mono -> resamples to 16k -> encode_batch().
    """
    wav_path = str(wav_path)

    # Load audio: waveform shape [channels, time]
    waveform, sr = torchaudio.load(wav_path)

    # Convert to mono if needed
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    # Resample to 16k (what most speaker encoders expect)
    target_sr = 16000
    if sr != target_sr:
        waveform = torchaudio.functional.resample(waveform, sr, target_sr)

    # SpeechBrain expects [batch, time] or [batch, channels, time] depending on version.
    # Most common: [batch, time]
    if waveform.dim() == 2:  # [1, time]
        batch_wav = waveform  # already mono
    else:
        batch_wav = waveform.squeeze(0)

    with torch.no_grad():
        emb = classifier.encode_batch(batch_wav)  # tensor

    # emb is usually [batch, 1, emb_dim] or [batch, emb_dim]
    emb = emb.squeeze().cpu().numpy()
    return emb



def merge_consecutive(rows):
    # rows: list of dicts with speaker_name + text
    out = []
    last = None
    for r in rows:
        if not r["text"].strip():
            continue
        if last and last["speaker_name"] == r["speaker_name"]:
            last["text"] = (last["text"] + " " + r["text"]).strip()
            last["end"] = r["end"]
        else:
            last = dict(r)
            out.append(last)
    return out


def main():
    if len(sys.argv) < 4:
        print("Usage: python identify_speakers.py output/utterances.json enroll_folder output/named_script.txt [--participants first1,last1,first2,last2,...]")
        sys.exit(1)

    utter_path = Path(sys.argv[1])
    enroll_dir = Path(sys.argv[2])
    out_txt = Path(sys.argv[3])
    
    # Parse optional --participants argument
    participant_names = None
    if "--participants" in sys.argv:
        idx = sys.argv.index("--participants")
        if idx + 1 < len(sys.argv):
            participant_names = [name.strip() for name in sys.argv[idx + 1].split(",")]
            print(f"Filtering enrollment files to participants: {participant_names}")

    if not utter_path.exists():
        raise FileNotFoundError(utter_path)
    if not enroll_dir.exists():
        raise FileNotFoundError(enroll_dir)

    utterances = json.loads(utter_path.read_text(encoding="utf-8"))

    # Find the base meeting wav used for slicing:
    # If your pipeline writes output/<stem>_16k.wav, infer it:
    stem = utter_path.stem.replace("_utterances", "")
    meeting_wav = Path("output") / f"{stem}_16k.wav"
    if not meeting_wav.exists():
        raise FileNotFoundError(f"Expected meeting wav at {meeting_wav}. Run your transcriber first.")

    # Compatibility shim:
    # Some SpeechBrain versions call `huggingface_hub.hf_hub_download(..., use_auth_token=...)`,
    # but newer huggingface_hub renamed that kwarg to `token`.
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
        pass

    # Load speaker embedding model (ECAPA)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    classifier = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        run_opts={"device": device},
    )

    # Build enrollment embeddings
    # Use lists to store multiple embeddings per person (for averaging)
    enroll_embs_dict = {}  # name_normalized -> list of embeddings
    tmp_enroll = Path("output") / "_enroll_wavs"
    tmp_enroll.mkdir(parents=True, exist_ok=True)

    def get_audio_duration(file_path: Path) -> float:
        """Get audio file duration in seconds using ffprobe"""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(file_path)
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
            pass
        return 0.0

    for p in sorted(enroll_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in [".wav", ".mp3", ".m4a", ".mp4", ".mov", ".aac", ".flac", ".ogg", ".webm"]:
            # Only use files >= 30 seconds for voice recognition
            duration = get_audio_duration(p)
            if duration < 30.0:
                print(f"Skipping enrollment file (too short: {duration:.1f}s < 30s): {p.name}")
                continue
            
            # Extract name from filename: "firstname,lastname.ext" or "username.ext" or with (2), (3), etc.
            # Name is everything before the first dot (extension) or parenthesis
            name = p.stem.lower().strip()
            name_normalized = re.sub(r"\(\d+\)", "", name)  # Remove (2), (3), etc. for matching
            name_normalized = name_normalized.strip()  # Clean up
            
            # If participant_names is specified, only process files matching those participants
            if participant_names is not None:
                # Check if this file matches any participant
                matches = False
                for participant_name in participant_names:
                    # Participant names are in "firstname,lastname" format (lowercase)
                    participant_normalized = participant_name.lower().strip()
                    
                    # Match if:
                    # 1. Exact match (e.g., "bobby,jones" == "bobby,jones")
                    # 2. Username match (e.g., "bobbyjones" matches if participant is "bobby,jones")
                    if name_normalized == participant_normalized:
                        matches = True
                        break
                    # Also check if filename is username format (no comma) and matches firstname,lastname
                    elif "," not in name_normalized and "," in participant_normalized:
                        # Remove comma from participant name to get username
                        username_from_participant = participant_normalized.replace(",", "").replace(" ", "")
                        if name_normalized == username_from_participant:
                            matches = True
                            break
                    # Or if filename has comma but participant is username (backward compatibility)
                    elif "," in name_normalized and "," not in participant_normalized:
                        # Remove comma from filename to get username
                        username_from_filename = name_normalized.replace(",", "").replace(" ", "")
                        if username_from_filename == participant_normalized:
                            matches = True
                            break
                
                if not matches:
                    print(f"Skipping enrollment file (not a participant): {p.name}")
                    continue
            
            # Convert to wav and create embedding
            wav = tmp_enroll / f"{name_normalized}_{p.stem}.wav"  # Include original stem to avoid conflicts
            to_wav_16k_mono(p, wav)
            emb = embed(classifier, wav)
            
            # Store multiple embeddings per person (will average later)
            if name_normalized not in enroll_embs_dict:
                enroll_embs_dict[name_normalized] = []
            enroll_embs_dict[name_normalized].append(emb)
            print(f"Enrolled speaker: {name_normalized} (from {p.name}, {duration:.1f}s)")

    if not enroll_embs_dict:
        raise RuntimeError("No enrollment audio files found in enroll folder.")
    
    # Average embeddings for each person (better voice recognition with multiple samples)
    enroll_embs = {}
    for name_normalized, emb_list in enroll_embs_dict.items():
        if len(emb_list) == 1:
            enroll_embs[name_normalized] = emb_list[0]
        else:
            # Average all embeddings for this person
            avg_emb = np.mean(emb_list, axis=0)
            enroll_embs[name_normalized] = avg_emb
            print(f"Averaged {len(emb_list)} enrollment files for {name_normalized}")

    # For each diarized utterance, slice audio, embed, and match to enrolled speaker
    tmp_segs = Path("output") / "_seg_wavs"
    tmp_segs.mkdir(parents=True, exist_ok=True)

    # Configuration for speaker matching thresholds
    # These can be overridden via environment variables or command-line args
    SPEAKER_MATCH_THRESHOLD = float(os.getenv("SPEAKER_MATCH_THRESHOLD", "0.75"))  # Minimum cosine similarity
    SPEAKER_MATCH_MARGIN = float(os.getenv("SPEAKER_MATCH_MARGIN", "0.05"))  # Minimum gap between top-1 and top-2
    
    # Track unknown speakers: map diarization speaker ID -> Unknown Speaker N
    # We'll use the diarization speaker label (A, B, C, etc.) to track unknowns
    unknown_speaker_map = {}  # diarization_speaker_id -> "Unknown Speaker N"
    unknown_counter = 1  # Next unknown speaker number
    
    # Track embeddings for unknown speakers to ensure consistency
    unknown_embeddings = {}  # "Unknown Speaker N" -> list of embeddings (for averaging)
    
    labeled = []
    for i, u in enumerate(utterances):
        start = float(u["start"])
        end = float(u["end"])
        txt = (u.get("text") or "").strip()
        if not txt:
            continue

        # Skip ultra-short segments (they embed poorly)
        if end - start < 0.6:
            continue

        seg_wav = tmp_segs / f"seg_{i:05d}.wav"
        slice_wav(meeting_wav, start, end, seg_wav)
        seg_emb = embed(classifier, seg_wav)

        # Get diarization speaker ID (A, B, C, etc.) for tracking unknowns
        diarization_speaker = u.get("speaker", f"SPEAKER_{i}")

        # Match against enrolled speakers
        scores = {}
        for name, e in enroll_embs.items():
            scores[name] = cosine(seg_emb, e)
        
        # Sort by score to find best match and margin
        sorted_scores = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        
        best_name = None
        best_score = -1e9
        second_best_score = -1e9
        
        if sorted_scores:
            best_name, best_score = sorted_scores[0]
            if len(sorted_scores) > 1:
                second_best_score = sorted_scores[1][1]
        
        # Check if match is confident enough
        score_gap = best_score - second_best_score
        is_confident_match = (best_score >= SPEAKER_MATCH_THRESHOLD and 
                             score_gap >= SPEAKER_MATCH_MARGIN)
        
        # Normalize speaker name: remove (2), (3) etc. if present
        if is_confident_match and best_name:
            normalized_name = re.sub(r"\(\d+\)", "", best_name).strip()
        else:
            # Low confidence or no match -> assign to unknown speaker
            # Use diarization speaker ID to track consistency
            if diarization_speaker not in unknown_speaker_map:
                # New unknown speaker - assign next number
                unknown_speaker_map[diarization_speaker] = f"Unknown Speaker {unknown_counter}"
                unknown_counter += 1
            
            normalized_name = unknown_speaker_map[diarization_speaker]
            
            # Store embedding for this unknown speaker (for future consistency checks)
            if normalized_name not in unknown_embeddings:
                unknown_embeddings[normalized_name] = []
            unknown_embeddings[normalized_name].append(seg_emb)
        
        labeled.append({
            "start": start,
            "end": end,
            "speaker_name": normalized_name,
            "score": best_score,
            "text": txt,
            "is_unknown": normalized_name.startswith("Unknown Speaker"),
            "diarization_speaker": diarization_speaker
        })

    # Merge consecutive lines for readability
    labeled = merge_consecutive(labeled)

    # Load username->name mapping from users.csv if available
    username_to_name = {}
    users_csv = Path("input") / "users.csv"
    if users_csv.exists():
        try:
            import csv
            with open(users_csv, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    username = (row.get("username") or "").strip().lower()
                    first = (row.get("first") or "").strip()
                    last = (row.get("last") or "").strip()
                    if username and first and last:
                        username_to_name[username] = f"{first} {last}"
        except Exception as e:
            print(f"Warning: Could not load username mapping: {e}")
    
    # Write script with proper name formatting
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for r in labeled:
        speaker_name = r['speaker_name']
        is_unknown = r.get('is_unknown', False)
        
        # Handle unknown speakers (keep as "Unknown Speaker N")
        if is_unknown or speaker_name.startswith("Unknown Speaker"):
            # Keep unknown speaker labels as-is
            formatted_name = speaker_name
        elif speaker_name != "Unknown":
            # Convert username to "First Last" format using mapping
            # Remove any (2), (3) etc. patterns first
            speaker_name_clean = re.sub(r"\(\d+\)", "", speaker_name).strip()
            # Look up in username mapping
            if speaker_name_clean in username_to_name:
                formatted_name = username_to_name[speaker_name_clean]
            elif "," in speaker_name_clean:
                # Fallback: old format "first,last"
                parts = speaker_name_clean.split(",")
                if len(parts) == 2:
                    first = parts[0].strip().capitalize()
                    last = parts[1].strip().capitalize()
                    formatted_name = f"{first} {last}"
                else:
                    formatted_name = speaker_name_clean.capitalize()
            else:
                # Just capitalize if no mapping found
                formatted_name = speaker_name_clean.capitalize()
        else:
            # Legacy "Unknown" -> convert to "Unknown Speaker 1" for consistency
            formatted_name = "Unknown Speaker 1"
        
        lines.append(f"{formatted_name}: {r['text']}")
    out_txt.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    
    # Print summary of unknown speakers
    unknown_speakers_found = [r['speaker_name'] for r in labeled if r.get('is_unknown', False) or r['speaker_name'].startswith("Unknown Speaker")]
    if unknown_speakers_found:
        unique_unknowns = sorted(set(unknown_speakers_found))
        print(f"Identified {len(unique_unknowns)} unknown speaker(s): {', '.join(unique_unknowns)}")

    # Also write a JSON if you want to inspect confidence
    out_json = out_txt.with_suffix(".json")
    out_json.write_text(json.dumps(labeled, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote:\n  {out_txt}\n  {out_json}")


if __name__ == "__main__":
    main()
