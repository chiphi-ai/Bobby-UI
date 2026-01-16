from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import requests

from .audio import to_wav_16k_mono

API_BASE = "https://api.assemblyai.com/v2"
CONF_THRESHOLD = 0.65


def upload_audio(wav_path: Path, api_key: str) -> str:
    headers = {"authorization": api_key}
    with wav_path.open("rb") as f:
        r = requests.post(f"{API_BASE}/upload", headers=headers, data=f)
    r.raise_for_status()
    upload_url = r.json().get("upload_url")
    if not upload_url:
        raise RuntimeError("Upload response missing upload_url")
    return upload_url


def submit_transcript(
    upload_url: str,
    api_key: str,
    speakers_expected: Optional[int] = None,
    speech_threshold: Optional[float] = None,
) -> str:
    headers = {"authorization": api_key, "content-type": "application/json"}
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
    r = requests.post(f"{API_BASE}/transcript", headers=headers, json=payload)
    r.raise_for_status()
    tid = r.json().get("id")
    if not tid:
        raise RuntimeError("Transcript submit response missing id")
    return tid


def poll_transcript(tid: str, api_key: str, poll_seconds: int = 3, timeout_seconds: int = 60 * 60) -> Dict:
    headers = {"authorization": api_key}
    start = time.time()
    while True:
        r = requests.get(f"{API_BASE}/transcript/{tid}", headers=headers)
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status == "completed":
            return data
        if status == "error":
            raise RuntimeError(f"Transcription failed: {data.get('error')}")
        if time.time() - start > timeout_seconds:
            raise TimeoutError("Transcription timed out")
        time.sleep(poll_seconds)


def transcribe_audio(
    audio_path: Path,
    output_dir: Path,
    api_key: str,
    speakers_expected: Optional[int] = None,
    speech_threshold: Optional[float] = None,
) -> Dict:
    wav_path = to_wav_16k_mono(audio_path, output_dir)
    upload_url = upload_audio(wav_path, api_key)
    tid = submit_transcript(upload_url, api_key, speakers_expected, speech_threshold)
    return poll_transcript(tid, api_key)


def _compute_speaker_probs(utterances: List[dict], words: List[dict]) -> Dict[int, Dict[str, float]]:
    speaker_conf_by_utt = {}
    for word in words:
        w_start = word.get("start", 0)
        w_end = word.get("end", 0)
        w_conf = float(word.get("confidence", 0.0))
        w_speaker = word.get("speaker") or word.get("speaker_label") or "Unknown"
        for idx, utt in enumerate(utterances):
            u_start = utt.get("start", 0)
            u_end = utt.get("end", 0)
            if u_start <= w_start < u_end or u_start < w_end <= u_end:
                if idx not in speaker_conf_by_utt:
                    speaker_conf_by_utt[idx] = {}
                speaker_conf_by_utt[idx][w_speaker] = speaker_conf_by_utt[idx].get(w_speaker, 0.0) + w_conf
                break
    probs = {}
    for idx, confs in speaker_conf_by_utt.items():
        total = sum(confs.values())
        if total <= 0:
            continue
        probs[idx] = {spk: c / total for spk, c in confs.items()}
    return probs


def _cluster_unknown_speakers(utterances: List[dict]) -> None:
    unknown_idxs = [i for i, u in enumerate(utterances) if u["speaker_label"].startswith("Unknown Speaker")]
    if len(unknown_idxs) <= 1:
        return
    clusters = []
    current = [unknown_idxs[0]]
    for prev_idx, idx in zip(unknown_idxs, unknown_idxs[1:]):
        prev = utterances[prev_idx]
        cur = utterances[idx]
        gap = (cur["start_ms"] - prev["end_ms"]) / 1000.0
        if gap > 30.0:
            clusters.append(current)
            current = [idx]
        else:
            current.append(idx)
    clusters.append(current)
    for cluster_num, cluster in enumerate(clusters, 1):
        for idx in cluster:
            utterances[idx]["speaker_label"] = f"Unknown Speaker {cluster_num}"


def extract_utterances(aai_response: Dict) -> List[Dict]:
    utterances = aai_response.get("utterances", [])
    words = aai_response.get("words", [])

    speaker_probs = _compute_speaker_probs(utterances, words)
    all_speakers = list({u.get("speaker", "Unknown") for u in utterances})

    results = []
    for idx, utt in enumerate(utterances):
        api_conf = utt.get("confidence")
        if api_conf is None:
            api_conf = 0.0
        confidence = float(api_conf)
        confidence_pct = int(confidence * 100)
        raw_speaker = utt.get("speaker", "Unknown")

        sp_probs = speaker_probs.get(idx)
        if not sp_probs:
            assigned_prob = 0.85
            other_count = max(1, len(all_speakers) - 1)
            sp_probs = {raw_speaker: assigned_prob}
            for sp in all_speakers:
                if sp != raw_speaker:
                    sp_probs[sp] = 0.15 / other_count

        speaker_label = raw_speaker
        if confidence < CONF_THRESHOLD:
            speaker_label = "Unknown Speaker 1"

        results.append(
            {
                "id": str(uuid.uuid4()),
                "idx": idx,
                "start_ms": int(utt.get("start", 0)),
                "end_ms": int(utt.get("end", 0)),
                "speaker_label": speaker_label,
                "raw_speaker_label": raw_speaker,
                "confidence": confidence_pct,
                "confidence_source": "api",
                "text": (utt.get("text") or "").strip(),
                "speaker_probs_json": json.dumps(sp_probs),
                "is_manually_edited": 0,
            }
        )

    _cluster_unknown_speakers(results)
    return results
