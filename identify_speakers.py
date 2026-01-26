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

# Load environment variables from .env (for HF_TOKEN)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Set HF token for huggingface_hub and login
hf_token = os.getenv('HF_TOKEN')
if hf_token:
    os.environ['HUGGING_FACE_HUB_TOKEN'] = hf_token
    os.environ['HF_HUB_TOKEN'] = hf_token
    os.environ['HF_TOKEN'] = hf_token
    try:
        from huggingface_hub import login
        login(token=hf_token, add_to_git_credential=False)
    except Exception:
        pass

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
    savedir = Path("pretrained_models/spkrec-ecapa-voxceleb")
    savedir.mkdir(parents=True, exist_ok=True)
    
    # Check for local cache first
    local_cache = Path.home() / ".cache/huggingface/hub/models--speechbrain--spkrec-ecapa-voxceleb"
    
    classifier = None
    try:
        # Try loading from HuggingFace with local_files_only first (use cached model)
        classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(savedir),
            run_opts={"device": device},
            use_auth_token=hf_token if hf_token else None,
        )
        print("Loaded speaker model successfully.")
    except Exception as e:
        print(f"Warning: Could not load model with default settings: {e}")
        # Try loading from local cache directly
        if local_cache.exists():
            print("Trying to load from local cache...")
            try:
                # Find the snapshot directory
                snapshots_dir = local_cache / "snapshots"
                if snapshots_dir.exists():
                    snapshot_dirs = list(snapshots_dir.iterdir())
                    if snapshot_dirs:
                        local_source = str(snapshot_dirs[0])
                        classifier = EncoderClassifier.from_hparams(
                            source=local_source,
                            savedir=str(savedir),
                            run_opts={"device": device},
                        )
                        print(f"Loaded speaker model from local cache: {local_source}")
            except Exception as e2:
                print(f"Could not load from local cache: {e2}")
        
        if classifier is None:
            print("Trying alternative model loading...")
            try:
                classifier = EncoderClassifier.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb2",
                    savedir=str(savedir),
                    run_opts={"device": device},
                )
            except Exception as e2:
                print(f"Error loading speaker model: {e2}")
                print("Speaker identification will be skipped.")
                sys.exit(1)

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
            # Only use files >= 15 seconds for voice recognition
            duration = get_audio_duration(p)
            if duration < 15.0:
                print(f"Skipping enrollment file (too short: {duration:.1f}s < 15s): {p.name}")
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
    
    # Two-pass approach: 
    # 1. First pass: collect all match scores per diarization speaker
    # 2. Vote on best speaker per diarization speaker ID
    # 3. Apply the voted speaker to all utterances from that diarization speaker
    
    # First pass: collect scores for all utterances
    utterance_data = []  # List of (utterance_info, all_scores, diarization_speaker)
    diarization_speaker_scores = {}  # diarization_speaker -> {enrolled_name -> [scores]}
    
    print(f"Processing {len(utterances)} utterances...")
    
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

        # Get diarization speaker ID
        diarization_speaker = u.get("speaker", f"SPEAKER_{i}")

        # Match against enrolled speakers
        scores = {}
        for name, e in enroll_embs.items():
            scores[name] = cosine(seg_emb, e)
        
        # Store scores for voting
        if diarization_speaker not in diarization_speaker_scores:
            diarization_speaker_scores[diarization_speaker] = {n: [] for n in enroll_embs.keys()}
        for name, score in scores.items():
            diarization_speaker_scores[diarization_speaker][name].append(score)
        
        # Sort by score
        sorted_scores = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best_name = sorted_scores[0][0] if sorted_scores else None
        best_score = sorted_scores[0][1] if sorted_scores else -1
        
        utterance_data.append({
            "start": start,
            "end": end,
            "text": txt,
            "diarization_speaker": diarization_speaker,
            "best_name": best_name,
            "best_score": best_score,
            "all_scores": scores
        })
    
    # Second pass: vote on best speaker per diarization speaker
    # Use the average of top scores for each enrolled speaker
    diarization_to_identified = {}  # diarization_speaker -> (identified_name, avg_score)
    
    for diar_spk, name_scores in diarization_speaker_scores.items():
        # Calculate average score for each enrolled speaker
        avg_scores = {}
        for name, scores_list in name_scores.items():
            if scores_list:
                # Use weighted average - weight longer utterances more
                avg_scores[name] = sum(scores_list) / len(scores_list)
        
        if not avg_scores:
            continue
        
        # Find best match by average score
        sorted_avg = sorted(avg_scores.items(), key=lambda kv: kv[1], reverse=True)
        best_name, best_avg = sorted_avg[0]
        second_avg = sorted_avg[1][1] if len(sorted_avg) > 1 else 0
        
        # Check if confident enough (using lower threshold for aggregated scores - 0.65)
        AGGREGATED_THRESHOLD = float(os.getenv("SPEAKER_AGGREGATE_THRESHOLD", "0.65"))
        LOWER_THRESHOLD = float(os.getenv("SPEAKER_LOWER_THRESHOLD", "0.50"))
        LARGE_MARGIN = float(os.getenv("SPEAKER_LARGE_MARGIN", "0.20"))
        
        # Debug: print all avg scores for this diarization speaker
        print(f"  {diar_spk} scores: " + ", ".join(f"{n}={s:.3f}" for n, s in sorted_avg[:4]))
        
        margin = best_avg - second_avg
        
        # Accept match if:
        # 1. Score >= 0.65 AND margin >= 0.05 (standard case), OR
        # 2. Score >= 0.50 AND margin >= 0.20 (high confidence from large margin)
        standard_match = best_avg >= AGGREGATED_THRESHOLD and margin >= SPEAKER_MATCH_MARGIN
        high_margin_match = best_avg >= LOWER_THRESHOLD and margin >= LARGE_MARGIN
        
        if standard_match or high_margin_match:
            normalized_name = re.sub(r"\(\d+\)", "", best_name).strip()
            diarization_to_identified[diar_spk] = (normalized_name, best_avg)
            match_type = "standard" if standard_match else "high-margin"
            print(f"    ✓ {diar_spk} -> {normalized_name} (avg: {best_avg:.3f}, margin: {margin:.3f}, {match_type})")
        else:
            if best_avg < LOWER_THRESHOLD:
                reason = f"score too low ({best_avg:.3f} < {LOWER_THRESHOLD})"
            elif best_avg < AGGREGATED_THRESHOLD and margin < LARGE_MARGIN:
                reason = f"below threshold ({best_avg:.3f} < {AGGREGATED_THRESHOLD}) and margin not large enough ({margin:.3f} < {LARGE_MARGIN})"
            else:
                reason = f"margin too small ({margin:.3f} < {SPEAKER_MATCH_MARGIN})"
            print(f"    ✗ {diar_spk} not identified: {reason}")
    
    # Track unknown speakers for those not identified
    unknown_speaker_map = {}  # diarization_speaker_id -> "Speaker N"
    unknown_counter = 1
    
    # Third pass: apply voted speaker names to all utterances
    labeled = []
    for ud in utterance_data:
        diar_spk = ud["diarization_speaker"]
        
        if diar_spk in diarization_to_identified:
            normalized_name, avg_score = diarization_to_identified[diar_spk]
            is_unknown = False
            score = ud["best_score"]
        else:
            # Unknown speaker - use consistent numbering
            if diar_spk not in unknown_speaker_map:
                unknown_speaker_map[diar_spk] = f"Speaker {unknown_counter}"
                unknown_counter += 1
            normalized_name = unknown_speaker_map[diar_spk]
            is_unknown = True
            score = ud["best_score"]
        
        labeled.append({
            "start": ud["start"],
            "end": ud["end"],
            "speaker_name": normalized_name,
            "score": score,
            "text": ud["text"],
            "is_unknown": is_unknown,
            "diarization_speaker": diar_spk
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

    # Load global speaker profiles (enrollment_key -> display_name) if available
    speaker_profiles = {}
    try:
        profiles_path = Path("output") / "speaker_profiles.json"
        if profiles_path.exists():
            data = json.loads(profiles_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                speaker_profiles = data
    except Exception as e:
        print(f"Warning: Could not load speaker profiles: {e}")
    
    # Write script with proper name formatting
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for r in labeled:
        speaker_name = r['speaker_name']
        is_unknown = r.get('is_unknown', False)
        
        # Handle unknown speakers (keep as "Speaker N")
        if is_unknown or (speaker_name.startswith("Speaker ") and len(speaker_name) > 8 and speaker_name[8:].split()[0].isdigit()):
            # Keep unknown speaker labels as-is
            formatted_name = speaker_name
        elif speaker_name != "Unknown":
            # Convert username to "First Last" format using mapping
            # Remove any (2), (3) etc. patterns first
            speaker_name_clean = re.sub(r"\(\d+\)", "", speaker_name).strip()
            # Global speaker profile display name mapping (for non-user profiles)
            prof = speaker_profiles.get(speaker_name_clean.lower()) if isinstance(speaker_profiles, dict) else None
            if isinstance(prof, dict) and prof.get("display_name"):
                formatted_name = str(prof.get("display_name"))
            # Look up in username mapping
            elif speaker_name_clean in username_to_name:
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
            # Legacy "Unknown" -> convert to "Speaker 1" for consistency
            formatted_name = "Speaker 1"
        
        lines.append(f"{formatted_name}: {r['text']}")
    out_txt.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    
    # Print summary of unknown speakers
    unknown_speakers_found = [r['speaker_name'] for r in labeled if r.get('is_unknown', False) or (r['speaker_name'].startswith("Speaker ") and len(r['speaker_name']) > 8 and r['speaker_name'][8:].split()[0].isdigit())]
    if unknown_speakers_found:
        unique_unknowns = sorted(set(unknown_speakers_found))
        print(f"Identified {len(unique_unknowns)} unidentified speaker(s): {', '.join(unique_unknowns)}")

    # Also write a JSON if you want to inspect confidence
    out_json = out_txt.with_suffix(".json")
    out_json.write_text(json.dumps(labeled, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote:\n  {out_txt}\n  {out_json}")


if __name__ == "__main__":
    main()
