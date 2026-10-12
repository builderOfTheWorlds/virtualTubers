# Expressive Voices (Chatterbox) — High-Level Plan

Status: PLAN ONLY — nothing built. Date: 2026-10-12.
Supersedes: aussie_voice_pack_plan.md (Piper fine-tune). Reusable parts of it
are listed in §4.

Goal: tubers deliver lines with character (inflection, intonation, emotion),
including a custom Australian voice cloned from the user's mate, airing on the
existing Twitch streams.

## 1. Pipeline, end to end

```
 RECORD (mate, remote)          PREPARE (argyre)               SERVE (argyre)
 ─────────────────────          ────────────────               ──────────────
 1 consent signed               4 splitter + QC                 7 tts-chatterbox service
 2 pilot + emotion set  ──►     5 curate reference clips  ──►     POST /synthesize
 3 upload to shared folder        per voice × per emotion         {text, voice, emotion}
                                6 register in voices.yaml         returns WAV
                                                                       │
 SHOW PREP (offline, per airing, existing revoice.prepare_show)        ▼
 8 LLM writes spoken line + picks emotion tag per scene ──► 9 tts_client "chatterbox" backend
                                                                       │
 10 WAV + duration checkpointed per scene (voice_prep_checkpoint) ◄────┘
 11 finished airing saved to voiced_narration (Postgres, WAV bytes)
                                                                       │
 LIVE (unchanged)                                                      ▼
 12 replay.Performer paces visuals to measured WAV duration
 13 voice_gate (one voice at a time) ─► audio_player ─► PulseAudio sink `vout`
 14 ffmpeg (stream_supervisor) mixes vout + screen ─► Twitch RTMP
```

## 2. Steps

### A. Capture the voice (mate, remote; nothing exposed from argyre)
1. Consent: VOICE_CONSENT.md signed (updated scope: expressive/emotional
   synthesis, six Twitch channels, revocation = delete refs + model + data).
2. Recording, one long WAV per batch ("Number N" + pause + line, "again" for
   retakes — existing guide). Two sets:
   - Pilot (50 lines, existing pilot_50.txt) — setup/QC gate.
   - Emotion set: ~10 lines x 6 moods (neutral, excited, angry, sad,
     menacing/low, comedic/deadpan), performed, not read flat. NEW.
   - Rule flip vs the old guide: "perform it a bit" replaces "don't perform".
3. Upload to the shared folder (Drive etc.). Same transfer path as before.

### B. Turn recordings into voices (argyre)
4. Splitter (whisper finds "number N" anchors, keeps last take after "again",
   cuts, resamples) + QC (clipping, noise floor, duration-vs-text, WER).
5. Curate reference clips: for each voice, pick the best 5–15 s clip per mood.
   Stored as files + a small manifest (voice -> emotion -> clip). Human
   listening step; the mate has veto.
6. Register: config/voices.yaml entries gain `provider: chatterbox`,
   `ref_clips: {neutral: ..., angry: ...}`. Names still describe the VOICE
   (`aussie_male`), not a character. voice_registry --verify checks clip files.

### C. Synthesis service (argyre)
7. New `services/tts-chatterbox` modelled on `services/tts-gpu`: one shared GPU
   process, same POST /synthesize wire plus `emotion`, GET /health, compose
   profile opt-in, install.sh + uninstall.sh pair, README. Picks the ref clip
   for (voice, emotion), maps emotion -> `exaggeration`/`cfg_weight`.
   Optional ASR round-trip check; auto-retry on hallucinated output. Runs on
   an NGC PyTorch aarch64/sm_121 base (main technical risk).

### D. Per-line emotion (the part that makes it expressive)
8. revoice.narrate_scene already asks the LLM for the spoken line and takes a
   per-role `tone`. Extend it to also return one emotion from a FIXED vocab
   (reuse app/emotion.py EMOTIONS where it fits; add a TTS mapping table).
   Enforced in code: unknown tag -> neutral. This also lets the avatar's
   emotion pose match the voice.
9. tts_client gets a `chatterbox` backend in `_BACKENDS`; TTSClient.synthesize
   takes `emotion`. compute_scene_hash must include emotion, otherwise a
   resumed prep would reuse WAVs rendered with the wrong delivery.
10. Piper stays as the automatic fallback if the service is down (show must
    air). Known tradeoff: the fallback voice differs from the cloned one.

### E. Airing (almost unchanged)
11. Prep is offline: each scene's WAV + measured duration is checkpointed,
    then the finished airing is stored in `voiced_narration`. Replays use
    `narration: "reuse"` and never re-synthesize.
12. Performer paces visuals to measured WAV duration (target_duration), so a
    slower synth costs PREP time only, not stream latency.
13. voice_gate -> audio_player -> PulseAudio `vout` -> ffmpeg -> Twitch. No
    change.
14. Verify on the live channel (virtualtubers-stream-ops skill), not just
    from logs.

### F. Optional tier 2 (only if zero-shot accent is not good enough)
15. Fine-tune Chatterbox (or Orpheus) on 1–2 h of the mate's labelled
    recordings, training only while Ollama and vLLM are stopped.

## 3. Decision gates (cheapest first)
- G1 Bake-off: tuned Piper vs Kokoro vs Chatterbox on ~6 real Ashiorid lines.
  Confirms Chatterbox is worth building.
- G2 Accent test: a ~10 s clip from the mate through Chatterbox. Decides tier
  1 vs tier 2 before he records anything big.
- G3 Speed + memory on GB10: measure s/line and GPU memory beside vllm-agents
  (13 GB free, OOM history). A 4600-scene episode at N s/scene = prep time.
- G4 Pilot QC >= 90% pass.
- G5 Mate approves samples; user judges stream fit.

## 4. Reused from the Aussie plan
Consent doc, recording guide structure, pilot_50.txt, splitter + QC design,
shared-folder transfer (trust boundary), mate's veto, GB10 training-container
work, voices.yaml/registry integration, stream verification.
Dropped: Piper fine-tune (Phases 3, 5, 7 as written), espeak phonemizer risk.

## 5. Risks
- aarch64/sm_121 PyTorch build for Chatterbox (same class as onnxruntime-gpu).
- Memory: service beside vLLM on unified memory.
- Zero-shot accent drift on strong AU vowels (G2).
- Hallucinated syllables -> ASR check + retry.
- Perth watermark in every clip (acceptable? confirm).
- More convincing clone -> consent scope must be explicit.
- Emotion tag quality depends on the LLM; fixed vocab + code-side fallback.

## 6. Open questions
- Does the mate have a mic/OS decided? (unchanged from old plan)
- Which personas get cloned voices vs stock refs?
- Do campaign episodes (not just Rerun narration) carry per-line emotion
  today? Not checked — affects step 8.
- Keep the voice private, or publish with consent?
