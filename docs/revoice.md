# revoice

## Overview

The per-airing narration pass for Rerun Theater — the "persona re-voicing"
layer the replay pipeline was designed around. It takes a parsed episode
script ([session_log_parser.md](session_log_parser.md)) and produces a
**voiced show**: the script's events grouped into scenes, each with a
spoken line (boss voice or coder voice) and its synthesized audio.

It runs at showtime, per airing — never baked into the episode library —
so by default every re-run of the same episode gets fresh dialogue from
the local LLM. `tool_call` events are never altered: narration is
*additive*; the on-screen commands, edits, and outputs stay exactly what
the parser recorded.

Fresh-per-airing is still the default, but a voiced airing is no longer
throwaway: `app/replay_pane.py` caches the full show — spoken text **and**
synthesized WAV bytes — to Postgres via `app/narration_store.py`
(docs/narration_store.md). A `replay_request` with
`payload.narration: "reuse"` (docs/operator_commands.md) skips this
module's LLM + TTS entirely and rebuilds the show from that cache: scenes
are replanned deterministically with `plan_scenes` (so the structure still
matches the current script), then each scene's cached `narration` text and
WAV are reattached in place of a fresh `narrate_scene`/TTS call. It falls
back to a fresh call through this module whenever nothing usable is
cached.

This module is unchanged by **duet replay** (multi-worker airings,
docs/duet_replay.md) — a duet director runs this exact same narration pass
(or the same cache-reuse rebuild described above) once for the whole cast;
followers never call it at all, loading the director's already-persisted
scenes straight from `narration_store.load_airing` instead.

### Timing model (why this makes audio and visuals line up)

1. **Estimate** each scene's on-screen render time at base pacing
   (`replay.estimate_event_seconds`).
2. **Size the line to the screen time**: ask the LLM for roughly
   `seconds × 2.5` words (~150 wpm) — a scene with minutes of scrolling
   output gets enough narration to talk over the whole thing; a two-second
   beat gets one short sentence.
3. **Synthesize and measure**: the real audio duration comes back from
   `tts_client`. The performer then scales that scene's visual pacing so
   text and speech finish together — *audio anchors, visuals adapt*
   ([replay.md](replay.md)).

### Scene grammar

| Kind | Speaker | Source events |
|---|---|---|
| `boss` | boss | one `user_message` |
| `coder_talk` | coder | one `assistant_text` |
| `coder_work` | coder | a run of consecutive `tool_call`s (≤ 8 per scene) |

Every event may carry an optional `"speaker"` override. `plan_scenes`
reads it as `event.get("speaker") or "boss"` for a `user_message` and
`event.get("speaker") or "coder"` for `assistant_text`/`tool_call` — an
explicit value wins, a missing/`null`/empty one falls back to the
type-based default in the table above. A run of consecutive `tool_call`s
only merges into one `coder_work` scene while every event shares the same
speaker: the accumulator flushes *before* appending a tool_call whose
speaker differs from the chunk's (so a mid-run persona swap starts a
fresh scene instead of blending two personas' actions into one). The
`kind` values (`boss`/`coder_talk`/`coder_work`) still describe scene
*structure*, not persona — they're unaffected by which speaker is set.

Real parsed session scripts (`session_log_parser.py`) never set this key
— a recorded session is inherently one human and one assistant, so those
scripts always resolve to exactly `"boss"`/`"coder"` and behavior is
unchanged. Hand-authored episode scripts can set it explicitly to assign
distinct dialogue to any of the personas in `speaker_names` — see
`replays/sample.json` and [duet_replay.md](duet_replay.md).

## Signature

```python
def prepare_show(script, llm, tts, workdir, worker_name="KODI-7",
                 boss_name="the boss", speed=1.0, max_output_lines=24,
                 progress=None, speaker_names=None) -> list[dict]

def plan_scenes(events: list[dict]) -> list[dict]
def scene_visual_seconds(scene, max_output_lines, speed=1.0) -> float
def target_words(seconds: float) -> int
def narrate_scene(scene, llm, words, worker_name, boss_name, speaker_names=None) -> str
def fallback_narration(scene, max_words) -> str
```

## Parameters

- `script` (dict, required): a parsed episode script (`events` list).
- `llm` (object or None): anything with `complete(system_prompt, messages)`
  — `llm_client.build_llm_client(config)` in practice. `None` skips the LLM
  and uses template narration.
- `tts` (`TTSClient` or None): from `tts_client.build_tts_client`. `None`
  produces a narrated-but-silent show (text lines, no audio).
- `workdir` (path, required): where scene WAVs are written (a per-show
  temp dir; the caller owns cleanup).
- `speed` / `max_output_lines`: must match the Performer's settings so the
  word-count sizing reflects real screen time.
- `progress` (callable, optional): called with one message per scene —
  the theater pane prints these as a "preparing tonight's episode" screen.
- `speaker_names` (dict, optional): speaker id → display name, resolved
  by `_display_name(speaker, speaker_names, worker_name, boss_name)` for
  any event carrying a per-event `"speaker"` override (see Scene grammar
  above). Falls back to `boss_name`/`worker_name` for the `"boss"`/
  `"coder"` ids and to the raw speaker id as a last resort. Real parsed
  scripts never set `"speaker"`, so omitting this kwarg reproduces
  today's boss/coder-only behavior exactly.

## Return Value

`plan_scenes`' scene list, each annotated with:

- `narration` (str, always present) — the spoken line
- `audio` (`tts_client.Narration` or None) — path + measured duration;
  None means the scene performs silently

Pass the list straight to `Performer.perform(script, show=...)`.

## Dependencies

`replay.py` (the pacing estimator — kept in lockstep with the Performer's
handlers), and duck-typed `llm_client` / `tts_client` instances supplied by
the caller. Standard library otherwise.

## Usage Examples

The glue most callers want (builds LLM + TTS from a worker config):

```python
from replay import Performer, prepare_voiced_show
import tempfile

with tempfile.TemporaryDirectory() as workdir:
    show = prepare_voiced_show(script, worker_config, workdir,
                               worker_name="KODI-7", progress=print)
    Performer(worker_name="KODI-7").perform(script, show=show)
```

Direct use with explicit clients:

```python
from llm_client import build_llm_client
from tts_client import build_tts_client
from revoice import prepare_show

show = prepare_show(script, build_llm_client(config),
                    build_tts_client(config), "/tmp/show")
voiced = sum(1 for scene in show if scene["audio"])
```

## Error Handling

The show must always air, so every step degrades instead of raising:

- LLM unreachable / empty reply → `fallback_narration` builds the line
  from the (already-redacted) script text.
- TTS failure on a scene → that scene's `audio` is None (plays silent at
  normal pacing); reported via `progress`.
- Narration only ever sees parser-redacted material, so nothing new can
  leak to a broadcast pane.

## Changelog

- **v1.2.0** (2026-07-18): `plan_scenes` gained an optional per-event
  `"speaker"` override — `event.get("speaker") or "boss"`/`"coder"` when
  absent, so real parsed scripts (which never set it) are byte-for-byte
  unchanged. The `tool_call` accumulator now flushes before appending a
  tool_call whose speaker differs from the current chunk's, so a
  mid-run persona swap starts a fresh `coder_work` scene instead of
  merging two personas' actions. `_PROMPTS` collapsed the old
  `{boss_name}`/`{worker_name}` placeholders into one `{name}`
  placeholder and dropped the literal "an AI coder" phrasing in the
  `coder_talk`/`coder_work` templates for persona-neutral wording. New
  `_display_name(speaker, speaker_names, worker_name, boss_name)` helper
  resolves a scene's speaker id to its display name (explicit
  `speaker_names` override → the `boss_name`/`worker_name` backward-compat
  defaults → the raw speaker id). `narrate_scene` and `prepare_show` both
  gained an optional `speaker_names=None` kwarg threaded straight through
  to `_display_name`. Together this lets a hand-authored episode script
  (`replays/sample.json`) assign distinct dialogue to up to 6 personas —
  see [duet_replay.md](duet_replay.md)'s "Ownership & uncast-speaker
  defaulting" section for how the cast/display-name wiring plays out
  end-to-end.
- **v1.1.0** (2026-07-12): No code changes to this module, but `plan_scenes`
  gained a second caller: `replay_pane.load_reused_show` (see
  docs/narration_store.md, docs/replay_pane.md) uses it to replan a
  cached episode's scene structure — for a `narration: "reuse"` request,
  the resulting scenes get cached `narration` text and WAV audio
  reattached instead of a fresh `narrate_scene`/TTS pass.
- **v1.0.0** (2026-07-12): Initial version — scene planning, word budgets
  sized to screen time, LLM re-voicing with template fallback, per-scene
  TTS with silent-scene degradation. 15 tests.
