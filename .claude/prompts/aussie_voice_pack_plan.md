# Aussie Voice Pack — Collaborative Build Plan

Goal: build a custom Australian-accent Piper voice (.onnx + .onnx.json) from
recordings made by the user's Aussie mate, trained on argyre (GB10), and drop
it into virtualTubers as a normal registry voice (e.g. `aussie_male`).

Diagram: .claude/prompts/aussie_voice_pack_plan.html

Status: PLAN — nothing built yet. Date: 2026-10-02.

CHANGE 2026-10-02: the mate is non-technical, so the recorder side is now
"one long WAV per batch" made in Audacity or iPhone Voice Memos, with each
sentence read as "Number N" + pause + sentence (and "again" for retakes). It
replaces running piper-recording-studio locally. Mate-facing guide:
.claude/prompts/aussie_voice_pack/mate_recording_guide.md (pilot:
aussie_voice_pack/pilot_50.txt). Consequence: Phase 1 needs a SPLITTER on
argyre. It runs local whisper over the batch WAV, finds the "number N" anchors,
keeps the last take after any "again", cuts the clips, resamples them to
22.05 kHz mono, and then runs the normal QC. recorder/install.sh is dropped.

---

## 1. Why this shape

- Piper is what every worker already speaks (config/voices.yaml,
  services/tts-gpu). A custom Piper model is just another .onnx pair, so
  zero runtime code changes are needed — integration is a config line.
- No en_AU voice exists in the stock Piper catalog (checked voices.json
  2026-10-02: English = en_US + en_GB only). Fine-tuning is the path.
- Fine-tuning an existing medium checkpoint needs ~1–2 h of clean speech
  (usable results from ~30 min) vs 10+ h from scratch.
- The mate is remote and is not on the LAN, so the collaboration surface
  must NOT be argyre's Gitea (192.168.1.120 is LAN-only) and must NOT be a
  service exposed from argyre to the internet (trust boundary — see §7).

## 2. Who does what

| Step | Mate (Australia) | User + Hermes (argyre) |
|---|---|---|
| Consent + license | signs VOICE_CONSENT.md | drafts it |
| Setup | installs kit, does mic check | builds + ships the kit |
| Recording | reads prompts in sessions of 20–40 min | QC each batch, send feedback |
| Training | — | fine-tunes on GB10 |
| Review | listens to samples, approves or vetoes | renders samples |
| Release | — | registers the voice, airs a test show |

## 3. Shared workspace: new repo `aussieVoicePack`

A separate repo, NOT inside virtualTubers. Reasons: the mate only needs the
kit, not the stream stack, and the voice/data has its own license and consent
terms.

- Host: private GitHub repo under builderOfTheWorlds (the mate gets
  collaborator access). Code and docs go in git.
- Audio does NOT go in git. ~1.5 h of 22.05 kHz mono 16-bit WAV is ~240 MB
  per take set, and re-takes add up. Use one of these, decision D2:
  - (a) Shared cloud folder (Google Drive / Dropbox / OneDrive): simplest
    for a non-dev mate. **Recommended.**
  - (b) Private Hugging Face dataset repo: versioned, good for a dev mate.
  - (c) Git LFS on GitHub: 1 GB free quota, so it gets tight with re-takes.

Repo layout:

```
aussieVoicePack/
  README.md                 # mate-facing quick start (plain English)
  VOICE_CONSENT.md          # what the voice may be used for, revocation terms
  LICENSE                   # code license; model license stated in MODEL_CARD
  prompts/
    prompts_en_au.txt       # ~1200 lines, one sentence each, numbered
    pilot_50.txt            # first 50 for the pilot session
  recorder/
    install.sh / uninstall.sh   # Linux/mac; install.ps1 if mate is on Windows
    README.md               # how to run the recording studio
  tools/
    qc_batch.py             # checks a batch, writes qc_report.md
    package_batch.py        # zips batch + metadata.csv for upload
  train/                    # used on argyre only
    Dockerfile
    install.sh / uninstall.sh
    train.sh                # preprocess -> fine-tune -> export
    eval_samples.txt        # fixed sentences rendered after each checkpoint
  MODEL_CARD.md             # filled at release
```

## 4. Phases

### Phase 0 — Decisions and consent (half a day, both)
- D1 Base checkpoint: fine-tune from en_GB (non-rhotic, closer to AU
  vowels) or en_US lessac-medium (best-known checkpoint). Check what
  exists in huggingface.co/rhasspy/piper-checkpoints before deciding. Plan:
  run BOTH on the pilot data and A/B by ear (Phase 3).
- D2 Audio transfer: shared folder vs HF dataset (§3).
- D3 Mate's OS: decides whether the recorder ships install.sh or
  install.ps1.
- D4 Voice name and register: e.g. `en_AU-<name>-medium` →
  registry `aussie_male` (names describe the VOICE, not a character, per
  voices.yaml rules).
- VOICE_CONSENT.md, signed and committed. It covers: use in virtualTubers
  streams (6 Twitch channels), whether the model may be shared publicly,
  that the mate can revoke (delete the model + data), and no
  impersonation of the mate outside the show.

### Phase 1 — Build the recording kit (Hermes, ~1 day)
1. Recorder: use rhasspy `piper-recording-studio` (a browser-based prompt
   reader that writes one WAV per prompt and exports in the Piper/LJSpeech
   layout). Wrap it in recorder/install.sh + uninstall.sh: venv, deps, and a
   `run.sh` that opens http://localhost:8000. It runs ON THE MATE'S MACHINE,
   so nothing is exposed from argyre. Verify the upstream repo's current
   install steps before wrapping it, and pin a commit.
2. Prompt set, prompts_en_au.txt:
   - Start from a phonetically balanced English set (e.g. the prompts that
     ship with piper-recording-studio, or a Harvard/ARCTIC-style list).
   - Add ~15% Aussie-flavoured lines (place names, slang, numbers, dates)
     so the model learns natural AU prosody. Include the kinds of lines
     the show actually speaks: dialogue, exclamations, questions, narration.
   - Draft the extra lines with local Ollama (hermes3:70b), then
     hand-review them. No trademarked or copyrighted text.
   - Target: ~1200 prompts ≈ 1.5 h of speech at ~4–5 s/line.
3. tools/qc_batch.py checks each WAV and flags failures in qc_report.md:
   - sample rate / channels / bit depth (convert, don't reject)
   - clipping (peak ≥ -0.5 dBFS), too quiet (RMS < -35 dBFS)
   - noise floor in leading silence (> -50 dBFS = noisy room)
   - leading/trailing silence trimmed to ~0.2 s
   - duration vs text length (way too short or long = misread/skip)
   - optional: transcribe with whisper (local) and flag WER > 15%
     against the prompt (catches misreads automatically)
4. tools/package_batch.py: zip the batch, metadata.csv
   (`id|text`), and qc_report.md, ready to upload to the shared folder.
5. README.md for the mate: mic setup (USB mic or headset, NOT a laptop's
   built-in mic), quiet soft-furnished room, ~20 cm from the mic, same
   setup every session, read naturally (don't "perform"), water nearby,
   stop when your voice gets tired.
6. Tests: pytest for qc_batch (synthetic sine/noise/clipped WAVs via numpy)
   and for package_batch (metadata format). Medium complexity, so 3–4 cases each.

### Phase 2 — Pilot (mate ~30 min, Hermes ~1 h)
- Mate records pilot_50.txt and uploads it. Hermes runs QC and sends back
  concrete feedback (levels, noise, pacing, plosives).
- Gate: ≥ 90% of clips pass QC before full recording starts. Fixing the setup
  now is far cheaper than re-recording 1200 lines later.

### Phase 3 — Early fine-tune smoke test (Hermes, ~half day)
Do this BEFORE the mate spends hours recording, to prove the pipeline works
end to end on GB10.
1. train/Dockerfile: NGC PyTorch base image for aarch64 + sm_121 (stock
   PyPI torch wheels may lack GB10 kernels — same class of problem as the
   onnxruntime-gpu work in services/tts-gpu). Install piper training from
   OHF-Voice/piper1-gpl (`python -m piper.train`) and espeak-ng. Check
   their TRAINING.md for the current commands and pin the version.
2. train/install.sh + uninstall.sh (project convention): build the image,
   make the work dirs; uninstall removes only the image and scratch dirs,
   and deleting datasets/checkpoints needs `--purge-data` + typed confirm.
3. Before any training run: stop Ollama and do NOT run beside
   vllm-hermes3-70b (2026-09 OOM history on argyre). train.sh checks this
   and refuses to start if it finds them.
4. Fine-tune both D1 candidates on the 50 pilot clips for a few hundred
   steps, then render eval_samples.txt. It will sound rough, but the run
   proves preprocess → train → export → `piper` playback works.
5. Disk: 113 GB free on argyre — checkpoints are ~800 MB each; keep last 3.

### Phase 4 — Full recording (mate, 4–8 sessions over ~2 weeks)
- Batches of ~150–200 prompts per session; upload after each one.
- Hermes runs QC within a day of each upload and posts a short report
  (a GitHub issue per batch in aussieVoicePack works well for async
  feedback across time zones). Failed clips go into a `retakes.txt` for
  the next session.
- Done when ≥ 1000 clips pass QC (≈ 1.2 h+).

### Phase 5 — Train (Hermes, overnight runs)
- Fine-tune the D1 winner on the full set. Checkpoint every ~1000 steps.
  After each checkpoint, render eval_samples.txt to `samples/step_<n>/`.
- Typical fine-tune: a few thousand steps on top of the base. Stop when
  the samples stop improving (listen, don't just watch loss).
- Export best checkpoint → `en_AU-<name>-medium.onnx` + `.onnx.json`.

### Phase 6 — Review loop (both)
- Share samples in the shared folder: same eval sentences, base model vs
  new model, plus 3–4 real show lines from a recent episode.
- Mate approves (or vetoes) — it's their voice. User judges stream fit.
- Common fixes: more data in weak areas (questions, numbers), adjust
  length_scale for pace, retrain from a different checkpoint.

### Phase 7 — Integrate into virtualTubers (Hermes, ~1 h)
1. Copy the model pair to virtualTubers/voices/ (gitignored, synced by
   hand per docs/tts_client.md).
2. config/voices.yaml: add
   `aussie_male: {provider: piper, model_path: /data/voices/en_AU-<name>-medium.onnx}`
3. scripts/download_voices.py: currently only knows the rhasspy HF
   BASE_URL. Add an optional per-voice source URL (the private HF repo or a
   release asset), so a fresh host can fetch the custom voice too. Add a
   test for the new URL branch.
4. `python3 app/voice_registry.py --verify` → must pass.
5. Restart tts-gpu (it preloads `all` from the data dir) and check that
   /health lists the new voice. POST /synthesize a line and listen to it.
6. Bind it to a persona in one show header, air a short test episode on
   one channel, and confirm it on the live Twitch output
   (virtualtubers-stream-ops skill).
7. Docs: add a "custom voices" section to docs/tts_client.md, and
   MODEL_CARD.md in aussieVoicePack (data size, base checkpoint, license,
   consent reference, version).

## 5. Timeline (rough)

| Week | Milestone |
|---|---|
| 1 | Phase 0–1 done, kit shipped; pilot recorded; smoke-test fine-tune works |
| 2–3 | Full recording in batches with rolling QC |
| 3–4 | Full training + review loop |
| 4 | Voice registered and airing |

## 6. Acceptance criteria
- ≥ 1000 QC-passed clips, consent signed.
- The mate approves the voice; the user judges it suitable for stream.
- Speed: synthesises in tts-gpu at roughly the current ~0.1–0.15 s/line
  (a medium model, same as the existing voices).
- `voice_registry.py --verify` passes; `/health` lists the voice; a test
  episode airs with it on Twitch.

## 7. Risks / tradeoffs (flagged explicitly)
- **Trust boundary:** recording happens on the mate's own machine, and
  data moves through a shared folder. Nothing on argyre is exposed to the
  internet. The tempting alternative — hosting the recording studio on
  argyre behind a public URL — is rejected: it would open the box to the
  internet just for convenience.
- **GB10 training stack:** torch/aarch64/sm_121 compatibility is the main
  technical risk. Phase 3 exists to hit it early, before hours of
  recording go in.
- **Memory contention:** training shares unified memory with the LLMs, so
  it runs only while Ollama/vLLM are stopped. This means generator jobs
  get scheduled around training.
- **No en-au espeak phonemizer:** Piper phonemizes via espeak-ng, which
  has en-us/en-gb but no Australian voice. Fine-tuning still learns the
  accent from the audio, but some vowels may lean toward the base accent.
  The D1 A/B tests this; using en-gb phonemes is the likely better fit.
- **Voice rights:** a cloned real voice airing publicly. The consent doc
  plus the mate's veto at Phase 6 cover this; revocation means deleting
  the model and data.
- **Recording consistency** is the #1 quality factor (same mic, room,
  distance, energy across sessions). The pilot gate and per-batch QC
  enforce it.

## 8. Open questions for the user
- Mate's OS, and how techy are they? (decides install.sh vs install.ps1 vs
  a "just open this link" setup)
- Audio transfer: shared folder vs HF dataset (D2)?
- Male or female voice / which persona slot is it meant for?
- Keep the model private, or publish it (e.g. HF) with the mate's consent?
