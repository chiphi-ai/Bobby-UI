# How to Improve AssemblyAI Transcription & Diarization

Since you're using AssemblyAI's API (not training your own models), here are practical ways to improve accuracy:

## 1. Enable AssemblyAI Premium Features (API Settings)

### A. Custom Vocabulary / Word Boosting
**What it does:** Tells AssemblyAI to recognize specific words (company names, people names, technical terms) more accurately.

**How to implement:**
- Create a custom vocabulary file with your domain-specific terms
- Pass it to AssemblyAI API via `word_boost` parameter
- This is especially helpful for:
  - Company names (e.g., "PhiAI", "Link Studio")
  - People names (enrolled participants)
  - Technical terms, product names, acronyms

**Code changes needed:**
- Add `word_boost` parameter to `submit_transcript()` in `transcribe_assemblyai.py`
- Load vocabulary from a config file (e.g., `custom_vocabulary.txt`)

### B. Language Model Enhancement
AssemblyAI offers different models. Check if you can:
- Use `model_type: "best"` for highest accuracy (may cost more)
- Enable `auto_highlights: True` for better context understanding
- Enable `sentiment_analysis: True` (helps with punctuation/intonation)

### C. Audio Quality Settings
- `audio_start_from`: Skip silence at beginning
- `audio_end_at`: Skip silence at end
- `filter_profanity: False` (if you want exact transcription)

## 2. Pre-Process Audio Before Sending to AssemblyAI

**Current:** You convert to 16k mono (good!)

**Additional improvements:**

### A. Audio Denoising
- Use `ffmpeg` to apply noise reduction before transcription
- Reduces background noise that confuses ASR
- Command: `ffmpeg -i input.wav -af "highpass=f=200,lowpass=f=3000,afftdn=nr=10" output.wav`

### B. Normalize Audio Levels
- Ensure consistent volume across the meeting
- Prevents quiet speakers from being missed
- Command: `ffmpeg -i input.wav -af "loudnorm=I=-16:TP=-1.5:LRA=11" output.wav`

### C. Remove Long Silences
- AssemblyAI charges by audio length
- Removing silence saves money + improves accuracy
- Command: `ffmpeg -i input.wav -af "silenceremove=start_periods=1:start_duration=1:start_threshold=-50dB" output.wav`

## 3. Optimize Speaker Count Parameter

**Current:** You pass `--speakers` if known

**Improvement:**
- Always try to pass accurate speaker count
- AssemblyAI's diarization is much better with this hint
- If you have participant list, use `len(participants)` + 1-2 buffer for unknowns
- Example: 5 enrolled participants → pass `--speakers 6` or `7` to account for unknowns

## 4. Post-Process Transcription

### A. Custom Word Replacement Rules
- Fix common ASR errors (e.g., "Phi AI" → "PhiAI")
- Normalize numbers, dates, times
- Fix capitalization of proper nouns

### B. Speaker Name Normalization
- You already do this in `identify_speakers.py`
- Can add more rules for common name mispronunciations

## 5. Improve Your Speaker Matching (ECAPA Layer)

**Current:** You use ECAPA embeddings + cosine similarity

**Improvements:**

### A. Collect More Enrollment Data
- **Best ROI:** Get 2-3 enrollment samples per person (different mics, rooms, times)
- Average multiple embeddings (you already do this!)
- Longer enrollments (60-90s) are better than 30s minimum

### B. Calibrate Thresholds Per User
- Some voices are easier to match than others
- Track per-user false positive/negative rates
- Adjust `SPEAKER_MATCH_THRESHOLD` per person if needed

### C. Use Cluster-Level Matching
- Instead of matching each utterance independently:
  1. Group utterances by diarization speaker (A, B, C, etc.)
  2. Compute average embedding per cluster
  3. Match cluster centroid to enrolled speakers
  4. Apply label to all utterances in that cluster
- **Benefit:** More stable, fewer false switches

## 6. AssemblyAI API Features to Enable

Check AssemblyAI docs for these (may require API plan upgrade):

- **Dual Channel Transcription:** If you have stereo audio with separate mics
- **Speaker Separation:** Better diarization for overlapping speech
- **Entity Detection:** Automatically identifies people, organizations, locations
- **Topic Detection:** Helps with context understanding

## 7. Collect Feedback Data for Continuous Improvement

**What to track:**
- Which speakers are frequently misidentified?
- Which words are consistently wrong?
- Which meetings have poor diarization?

**How to use it:**
- Add those words to custom vocabulary
- Adjust speaker matching thresholds
- Improve enrollment audio quality for problematic speakers

## 8. Audio Source Quality (Hardware/Recording)

**Best practices:**
- Use dedicated microphones (not laptop mics)
- Record in quiet environments
- Use directional mics to reduce cross-talk
- If possible, record each speaker on separate channels

## Implementation Priority

**Quick wins (do first):**
1. ✅ Always pass accurate `--speakers` count
2. ✅ Add custom vocabulary for company/people names
3. ✅ Improve enrollment audio (more samples, longer duration)

**Medium effort:**
4. Audio pre-processing (denoising, normalization)
5. Cluster-level speaker matching
6. Post-processing word replacement rules

**Long-term:**
7. Collect feedback data
8. Per-user threshold calibration
9. Hardware improvements

## Next Steps

I can help you implement:
1. Custom vocabulary system (load from file, pass to AssemblyAI)
2. Audio pre-processing pipeline (denoising, normalization)
3. Cluster-level speaker matching (more stable labels)
4. Feedback collection system (track errors, improve over time)

Which would you like to tackle first?
