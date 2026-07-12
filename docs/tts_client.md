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
  silently". Env var `TTS_PROVIDER` overrides the config provider.
- `synthesize(text, out_wav, speaker)`:
  - `text` (str, required, non-empty): the line to speak.
  - `out_wav` (path, required): destination WAV (parent dirs created).
  - `speaker` (str, default `"coder"`): which `voice.speakers` override to
    merge over the base config; unknown speakers get the base voice.

### Config keys (worker.yaml `voice:` section)

| Key | Used by | Meaning |
|---|---|---|
| `provider` | all | `piper` \| `kokoro` (alias of piper) \| `openai` \| `elevenlabs` \| `null` |
| `model_path` | piper | Path to the `.onnx` voice model (`/data/voices/...` in-container) |
| `voice_id` | openai, elevenlabs | Cloud voice name / dashboard voice ID |
| `tts_model` | openai | `tts-1` (default) or `tts-1-hd` |
| `speakers.<name>` | all | Per-speaker overrides merged over the base keys |

API keys come from the environment only (`OPENAI_API_KEY`,
`ELEVEN_API_KEY`) — never from the config file, same policy as
`llm_client.py`.

## Return Value

`synthesize` returns a `Narration` — the written WAV's path and its
measured duration in seconds.

## Dependencies

Standard library (`wave`, `subprocess`, `shutil`). Piper needs the `piper`
CLI (`piper-tts` in `requirements.txt` — in the worker image) plus a
downloaded voice model (`scripts/download_voices.py`). The cloud backends
import `openai` / `elevenlabs` lazily and are **not** installed by default.
`ffprobe` is only touched when a backend emits a WAV the stdlib `wave`
module can't parse.

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
  model_path: /data/voices/en_US-lessac-medium.onnx   # the coder
  speakers:
    boss:
      model_path: /data/voices/en_US-ryan-high.onnx   # the boss
```

```python
boss_line = tts.synthesize("How's that bug fix coming along?",
                           "scene_000.wav", speaker="boss")
```

## Error Handling

Everything raises `TTSError` (a `RuntimeError`): unknown provider, empty
text, missing piper CLI or model file, missing cloud package/API key,
backend exit failures, and unmeasurable output. Callers in the replay
pipeline catch it per scene — a failed synthesis makes that scene silent,
never cancels the show.

## Changelog

- **v1.0.0** (2026-07-12): Initial version — piper/kokoro, openai,
  elevenlabs backends; measured durations via `wave` + ffprobe fallback;
  per-speaker voice overrides. 13 tests.
