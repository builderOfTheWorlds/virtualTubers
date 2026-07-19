# tts_client

## Overview

Provider-switchable text-to-speech, mirroring `llm_client.py`'s pattern:
read the worker config's `voice` section, get back a client with one
method. It exists to give Rerun Theater's replays spoken narration
([revoice.md](revoice.md)) — and, later, live agent voices.

The key design point: every backend renders to a WAV file, and
`synthesize()` returns the audio's **measured** duration (read from the WAV
header, ffprobe fallback). That measured number — not an estimate — is what
`replay.py` anchors its visual pacing to, so on-screen text and the spoken
line finish together.

**Piper is single-session, not per-call.** `_piper_local` goes through
Piper's Python API (`piper.voice.PiperVoice`) directly rather than shelling
out to the `piper` CLI, and keeps each loaded model resident in this worker
process for as long as it runs (`_LOCAL_VOICES`, keyed by resolved model
path) — the ONNX Runtime session init + model deserialization cost is paid
once ever per model, not once per scene. **Piper is also remote-ready**:
set `voice.base_url` (or the `TTS_BASE_URL` env override) to synthesize
against a separate `piper.http_server` process instead — e.g. to move TTS
onto a more powerful machine later — with no code change, just config.

Adapted from the autoVideo project's `src/tts/narrator.py` (kept local per
the shared-utilities rule; autoVideo's copy depends on its own
`ffmpeg_utils`/`logging_setup`).

**Two-voice support.** `voice.speakers.<name>` blocks override the base
voice per speaker, so the boss and the coder speak with different Piper
models (or cloud voice IDs) during replay dialogue.

## Signature

```python
class TTSClient:
    def __init__(self, voice_config: dict)
    def voice_for(self, speaker: str) -> dict
    def synthesize(self, text: str, out_wav: str | Path,
                   speaker: str = "coder") -> Narration

@dataclass
class Narration:
    audio_path: Path
    duration: float  # seconds, measured

def build_tts_client(config: dict) -> TTSClient | None
def wav_duration(path: str | Path) -> float
```

## Parameters

- `build_tts_client(config)`: a full worker config (its `voice` section is
  used) or a bare voice section. Returns **None** when voice is disabled
  (`provider: "null"`/`none`/missing) — callers treat None as "perform
  silently". Env var `TTS_PROVIDER` overrides the config provider; env var
  `TTS_BASE_URL` overrides `voice.base_url` (piper/kokoro only — see below).
- `synthesize(text, out_wav, speaker)`:
  - `text` (str, required, non-empty): the line to speak.
  - `out_wav` (path, required): destination WAV (parent dirs created).
  - `speaker` (str, default `"coder"`): which `voice.speakers` override to
    merge over the base config; unknown speakers get the base voice.

### Config keys (worker.yaml `voice:` section)

| Key | Used by | Meaning |
|---|---|---|
| `provider` | all | `piper` \| `kokoro` (alias of piper) \| `openai` \| `elevenlabs` \| `fake` (testing — see below) \| `null` |
| `model_path` | piper | Path to the `.onnx` voice model (`/data/voices/...` in-container). In remote mode, only its filename stem is sent (as `voice`) — the file itself doesn't need to exist in this container. |
| `base_url` | piper | Optional. Set to synthesize against a remote `piper.http_server` instead of locally (see below). Blank/unset: local single-session synthesis. |
| `rate` | piper | Speaking rate multiplier (1.0 = normal). Mapped to Piper's `length_scale` (inverse: higher rate = lower length_scale = faster), applied in both local and remote mode. |
| `voice_id` | openai, elevenlabs | Cloud voice name / dashboard voice ID |
| `tts_model` | openai | `tts-1` (default) or `tts-1-hd` |
| `speakers.<name>` | all | Per-speaker overrides merged over the base keys |

API keys come from the environment only (`OPENAI_API_KEY`,
`ELEVEN_API_KEY`) — never from the config file, same policy as
`llm_client.py`.

**`fake` (testing only).** Skips synthesis entirely — no subprocess, no
model load, no network — and writes a silent WAV sized to roughly how long
the line would take to read (`len(text.split()) / 2.5`s, floored at 0.5s),
so `Narration.duration`-anchored pacing and the duet cue/watchdog timings
(`docs/duet_replay.md`) still behave realistically instead of degenerating
to near-zero. This is **not** the same as `provider: "null"`/disabling
voice: a duet director treats "no TTS client at all" as a hard refusal
(voice prep unavailable), so `fake` — a real, non-None client that just
skips the expensive part — is the way to get a fast *and* duet-safe test
airing. Combine with `app/replay.py`'s `REPLAY_SKIP_LLM` (docs/duet_replay.md)
to skip the LLM narration rewrite too, for the fastest possible test prep.
Set via `voice.provider: fake` or the `TTS_PROVIDER=fake` env override —
never leave this set for a real stream, since no audio ever reaches
PulseAudio/ffmpeg.

## Return Value

`synthesize` returns a `Narration` — the written WAV's path and its
measured duration in seconds.

## Dependencies

Standard library (`wave`, `subprocess`, `shutil`). Local Piper synthesis
imports `piper.voice.PiperVoice` / `piper.config.SynthesisConfig`
(`piper-tts` in `requirements.txt` — in the worker image) plus a downloaded
voice model (`scripts/download_voices.py`); both are lazy imports so a
worker with `voice.provider: null`/`openai`/`elevenlabs` never pays for
`onnxruntime`. Remote Piper mode imports `httpx` (already a core dependency
via `llm_client.py`) instead. The cloud backends import `openai` /
`elevenlabs` lazily and are **not** installed by default. `ffprobe` is only
touched when a backend emits a WAV the stdlib `wave` module can't parse.

## Remote mode (`voice.base_url` / `TTS_BASE_URL`)

Piper ships its own HTTP server (`python -m piper.http_server -m <model>
--data-dir <dir>`) with a `/synthesize` endpoint that takes `{"text": ...,
"voice": ...}` and returns raw WAV bytes — `_piper_remote` just POSTs to it,
no custom server code needed. Run that on a separate (potentially more
powerful, e.g. GPU-equipped — `piper.http_server --cuda`) machine, put every
persona's `.onnx`/`.onnx.json` pair in its `--data-dir`, and point
`voice.base_url` (or `TTS_BASE_URL`) at `http://that-host:5000`. The `voice`
field sent in each request is `model_path`'s filename stem, so one remote
server can host every worker's voice and pick the right one per request —
no per-worker server needed. Local mode (blank/unset `base_url`, the
default) needs no server at all; it's purely in-process.

## Usage Examples

Replay narration (how `replay.py`/`revoice.py` use it):

```python
from tts_client import build_tts_client

tts = build_tts_client(worker_config)   # None -> silent show
if tts:
    line = tts.synthesize("Okay, running the test suite now...",
                          "/tmp/show/scene_004.wav", speaker="coder")
    print(line.duration)  # e.g. 3.84 — the pacing anchor
```

Two voices from one config:

```yaml
voice:
  provider: piper
  model_path: /data/voices/en_US-lessac-low.onnx   # the coder
  speakers:
    boss:
      model_path: /data/voices/en_US-ryan-low.onnx   # the boss
```

```python
boss_line = tts.synthesize("How's that bug fix coming along?",
                           "scene_000.wav", speaker="boss")
```

Same config, but synthesized on a remote machine instead of in-container:

```yaml
voice:
  provider: piper
  model_path: /data/voices/en_US-lessac-low.onnx   # -> "voice": "en_US-lessac-low"
  base_url: http://tts-box.local:5000
```

## Error Handling

Everything raises `TTSError` (a `RuntimeError`): unknown provider, empty
text, missing piper-tts package or model file, a failed remote request
(non-2xx from `base_url`), missing cloud package/API key, and unmeasurable
output. Piper's own synthesis exceptions (local or remote) are not wrapped
and propagate as whatever Piper/httpx raise. Callers in the replay pipeline
catch broadly per scene — a failed synthesis makes that scene silent, never
cancels the show.

## Changelog

- **v1.2.0** (2026-07-19): Piper now goes through the Python API
  (`piper.voice.PiperVoice`) instead of shelling out to the CLI, and caches
  one loaded model per worker process instead of reloading it on every
  scene (`_LOCAL_VOICES`). Added `voice.base_url`/`TTS_BASE_URL` — Piper can
  now synthesize against a remote `piper.http_server` instead of locally,
  with no code change. `voice.rate` is now actually honored (mapped to
  Piper's `length_scale`) in both modes — previously documented but unused.
  Deployed default voice models switched from medium/high to the `low`
  quality tier for faster synthesis (`scripts/download_voices.py`).
- **v1.1.0** (2026-07-19): Added the `fake` provider — instant, duet-safe
  silent synthesis for testing (see above).
- **v1.0.0** (2026-07-12): Initial version — piper/kokoro, openai,
  elevenlabs backends; measured durations via `wave` + ffprobe fallback;
  per-speaker voice overrides. 13 tests.
