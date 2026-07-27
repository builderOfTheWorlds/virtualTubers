# revoice

## Overview

The per-airing narration pass for Rerun Theater — the "persona re-voicing"
layer the replay pipeline was designed around. It takes a validated episode
([episode_schema.md](episode_schema.md)) — `meta`/`cast`/`scenes[]`, per
campaign_platform_contract.md §1 — and produces a **voiced show**: the episode's own scenes,
each given a spoken line and its synthesized audio.

It runs at showtime, per airing — never baked into the episode library —
so by default every re-run of the same episode gets fresh dialogue from
the local LLM. A scene's `render[]` entries are never altered: narration is
*additive*; the on-screen primitives stay exactly what the episode
scripted.

**Campaign-platform rewrite (2026-07-26).** This module used to own scene
*planning*: `plan_scenes` grouped a flat list of parsed session events
(`session_log_parser.py`) into `boss`/`coder_talk`/`coder_work` scenes,
because the parser only ever emitted one raw event per turn/tool call.
That grouping step is gone. A campaign generator (`generators/coder/`,
`generators/dnd/`, ...) now emits scene-sized units directly — `kind` and
`render[]` are already scene properties in the episode file — so
`prepare_show` simply iterates `episode['scenes']`. Screen-time estimation,
narration prompts, and fallback templates all moved from this module's own
hardcoded tables (`_PROMPTS`, per-`kind` branching in
`scene_visual_seconds`/`fallback_narration`) to config
(`config/campaigns/<campaign>/narration.yaml`) and to the shared rendering
engine (`app/primitives.py`), so a new campaign (a D&D story show, say)
needs a YAML file here, not a code change.

Fresh-per-airing is still the default, but a voiced airing is no longer
throwaway: `app/replay_pane.py` caches the full show — spoken text **and**
synthesized WAV bytes — to Postgres via `app/narration_store.py`
(docs/narration_store.md). A `replay_request` with
`payload.narration: "reuse"` (docs/operator_commands.md) skips this
module's LLM + TTS entirely and rebuilds the show from that cache:
`replay_pane._rebuild_scenes_from_rows` zips the cached rows against the
episode's own `scenes[]` by index (so the structure still matches the
current episode), then each scene's cached `narration` text and WAV are
reattached in place of a fresh `narrate_scene`/TTS call. It falls back to
a fresh call through this module whenever nothing usable is cached.

This module is unchanged by **duet replay** (multi-worker airings,
docs/duet_replay.md) — a duet director runs this exact same narration pass
(or the same cache-reuse rebuild described above) once for the whole cast;
followers never call it at all, loading the director's already-persisted
scenes straight from `narration_store.load_airing` instead.

### Timing model (why this makes audio and visuals line up)

1. **Estimate** each scene's on-screen render time at base pacing
   (`primitives.estimate_scene_seconds`, over the scene's own `render[]`
   recipes — summed under `mode: "sequence"`, maxed under `"parallel"`).
2. **Size the line to the screen time**: ask the LLM for roughly
   `seconds × 2.5` words (~150 wpm) — a scene with a long visual sequence
   gets enough narration to talk over the whole thing; a two-second beat
   gets one short sentence.
3. **Synthesize and measure**: the real audio duration comes back from
   `tts_client`. The performer then scales that scene's visual pacing so
   text and speech finish together — *audio anchors, visuals adapt*
   ([replay.md](replay.md)).

### Narration prompts + fallbacks live in config, keyed by `kind`

`config/campaigns/<campaign>/narration.yaml`:

```yaml
coder_work:
  prompt: |
    You are voicing {name}, live-streaming their work. Describe out loud,
    present tense, what you are doing in these recorded actions - about
    {words} words, enough to talk over the whole sequence:

    {material}
  fallback_template: "Okay — {render_summary}."
```

Token set available to both `prompt` and `fallback_template`:

| Token | Meaning |
|---|---|
| `{name}` | resolved on-screen speaker display name (see `SpeakerNames`/`_display_name` below) |
| `{words}` | target word count for this scene's screen time (`prompt` only) |
| `{text}` | the scene's own scripted text (`episode.scenes[i].text`) |
| `{material}` | `{text}` when non-empty, else `primitives.render_summary(scene, primitives)` — what the LLM is told is happening |
| `{render_summary}` | `primitives.render_summary(scene, primitives)` directly — a short description of the scene's `render[]` entries, used as the last-resort fallback for a scene with no scripted line |

`load_narration_config(campaign)` loads this file; a missing file (a
shared-only campaign) or any parse failure degrades to `{}` rather than
raising — `fallback_line`'s own scene-level `fallback` text and its
generic last resort keep a show airing even with no narration config at
all, the same soft-degradation contract as an LLM/TTS outage.

## Signature

```python
def load_narration_config(campaign, *, campaigns_dir=None) -> dict

def scene_visual_seconds(scene, primitives, *, max_output_lines=24, speed=1.0) -> float

def target_words(seconds: float) -> int

def fallback_line(scene, narration_config, primitives, max_words, names) -> str

def narrate_scene(scene, llm, words, names, narration_config, primitives,
                  verbatim=False) -> str

def prepare_show(episode, llm, tts, workdir, *, primitives, narration_config,
                 worker_name="KODI-7", boss_name="the boss", speed=1.0,
                 max_output_lines=24, progress=None, speaker_names=None,
                 verbatim=False) -> list[dict]

class SpeakerNames:
    worker_name: str = "KODI-7"
    boss_name: str = "the boss"
    speaker_names: dict = {}
```

**Deleted** (campaign_platform_contract.md §5 — the event-grouping layer): `plan_scenes`,
`_PROMPTS`, `fallback_narration`, `_scene_material`, `MAX_SCENE_EVENTS`.
There is nothing left to group — a generator already emits scene-sized
units — and `MAX_SCENE_EVENTS`'s old job (capping how much screen time one
spoken line has to cover) is now `config/validation.yaml`'s
`limits.max_scene_seconds` validator rule, checked once at ingest rather
than by silently regrouping behind the generator's back.

**Kept unchanged:** `WORDS_PER_SECOND=2.5`, `MIN_WORDS=8`, `MAX_WORDS=130`,
`target_words`, `_trim_words`, `_display_name`'s resolution order,
`SYSTEM_PROMPT`, and `prepare_show`'s soft-degradation contract.

## Parameters

- `episode` (dict, required): a normalized/validated episode — `meta`,
  `cast`, `scenes` (each with `speaker`/`kind`/`text`/`fallback`/`mode`/
  `render`), per campaign_platform_contract.md §1. `prepare_show` reads `episode['scenes']`
  directly.
- `primitives` (dict, required for every function above except
  `target_words`/`fallback_line` sans render): the merged recipe table
  for the episode's campaign (`primitives.load_primitives(campaign)`) —
  needed to estimate screen time (`scene_visual_seconds`) and to describe
  a scene's visuals to the LLM/fallback (`render_summary`, via
  `narrate_scene`/`fallback_line`).
- `narration_config` (dict, required): `load_narration_config(campaign)`'s
  output — `{kind: {prompt, fallback_template}}`.
- `llm` (object or None): anything with `complete(system_prompt, messages)`
  — `llm_client.build_llm_client(config)` in practice. `None` skips the LLM
  and uses `fallback_line`.
- `tts` (`TTSClient` or None): from `tts_client.build_tts_client`. `None`
  produces a narrated-but-silent show (text lines, no audio).
- `workdir` (path, required): where scene WAVs are written (a per-show
  temp dir; the caller owns cleanup).
- `speed` / `max_output_lines`: must match the Performer's settings so the
  word-count sizing reflects real screen time.
- `progress` (callable, optional): called with one message per scene —
  the theater pane prints these as a "preparing tonight's episode" screen.
- `names` (`SpeakerNames`): bundles `worker_name`/`boss_name`/
  `speaker_names` for `_display_name`'s resolution order (explicit
  `speaker_names` override → `boss_name`/`worker_name` for the `"boss"`/
  `"coder"` ids → the raw speaker id). `prepare_show`/`narrate_scene`'s
  callers (`replay.py`'s `prepare_voiced_show`, `replay_pane.py`) still
  take `worker_name`/`boss_name`/`speaker_names` as separate kwargs;
  `prepare_show` builds the `SpeakerNames` bundle internally.
- `verbatim` (bool, default `False`; from a worker's `voice.verbatim`
  config): skip the LLM entirely whenever a scene's own `text` is
  non-empty, and speak it in full, untrimmed, instead of a paraphrase
  sized to the scene's estimated screen time. A scene with no scripted
  text (pure visual business — `kind` no longer implies this the way
  `coder_work` used to) always goes through the LLM/fallback path
  regardless of this flag — replaces the old `kind in ("boss",
  "coder_talk")` check now that `kind` is campaign-defined rather than a
  fixed set. A verbatim line that runs longer than the estimate doesn't
  desync anything — the audio-anchored pacing ([replay.md](replay.md))
  just holds the scene a bit longer for the voice to finish once the
  visual-pacing clamp is hit, same as any scene where the spoken line
  runs long.

## Return Value

`prepare_show` returns `episode['scenes']` — the SAME list, annotated in
place (no grouping/copy step any more) — each scene gaining:

- `narration` (str, always present) — the spoken line
- `audio` (`tts_client.Narration` or None) — path + measured duration;
  None means the scene performs silently

Pass the list straight to `Performer.perform(episode, show=...)`.

`fallback_line` and `narrate_scene` return a single spoken line (str,
never empty) — the last-resort text a show speaks when the LLM is down or
unconfigured.

## Dependencies

`app/primitives.py` (`estimate_scene_seconds`, `render_summary` — the
screen-time estimator and visuals-description helper, both frozen per
campaign_platform_contract.md §2), `PyYAML` (narration config), and duck-typed `llm_client` /
`tts_client` instances supplied by the caller. Standard library otherwise.

## Usage Examples

The glue most callers want (builds LLM + TTS + primitives/narration config
from a worker config and a campaign name):

```python
from replay import Performer, prepare_voiced_show
import tempfile

with tempfile.TemporaryDirectory() as workdir:
    show = prepare_voiced_show(episode, worker_config, workdir,
                               worker_name="KODI-7", campaign="coder",
                               progress=print)
    Performer(worker_name="KODI-7", primitives=primitives_table).perform(episode, show=show)
```

Direct use with explicit clients and config:

```python
from llm_client import build_llm_client
from tts_client import build_tts_client
from primitives import load_primitives
from revoice import load_narration_config, prepare_show

primitives = load_primitives("coder")
narration_config = load_narration_config("coder")
show = prepare_show(episode, build_llm_client(config), build_tts_client(config),
                    "/tmp/show", primitives=primitives, narration_config=narration_config)
voiced = sum(1 for scene in show if scene["audio"])
```

## Error Handling

The show must always air, so every step degrades instead of raising:

- LLM unreachable / empty reply / no prompt configured for a scene's
  `kind` → `fallback_line` builds the line from the scene's own
  `fallback` text, or a `narration_config` `fallback_template`, or a
  generic last resort built from `render_summary`.
- A broken `fallback_template` (references a token this module doesn't
  supply) degrades to the raw template text rather than raising — this
  function IS the last line of defense.
- A missing or malformed `narration.yaml` degrades `load_narration_config`
  to `{}` rather than raising.
- TTS failure on a scene → that scene's `audio` is None (plays silent at
  normal pacing); reported via `progress`.
- Narration only ever sees already-validated (and, when
  `config/validation.yaml`'s `redaction.on_match` is `"redact"`, already-
  scrubbed) episode text, so nothing new can leak to a broadcast pane.

## Changelog

- **v2.0.0** (2026-07-26, campaign-platform build, campaign_platform_contract.md §5):
  **`plan_scenes`, `_PROMPTS`, `fallback_narration`, `_scene_material`,
  `MAX_SCENE_EVENTS` deleted.** `prepare_show` now iterates
  `episode['scenes']` directly — no grouping step, since a campaign
  generator already emits scene-sized units. New `load_narration_config`
  (loads `config/campaigns/<campaign>/narration.yaml`), `fallback_line`
  (scene `fallback` → narration config `fallback_template` →
  `render_summary`-based generic last resort), new `SpeakerNames`
  dataclass bundling `worker_name`/`boss_name`/`speaker_names` for
  `_display_name`/`narrate_scene`/`fallback_line`. `scene_visual_seconds`
  is now a thin delegate to `primitives.estimate_scene_seconds` (kept as
  the public name since `replay_pane.py` and tests already use it).
  `verbatim` now keys off "does this scene have non-empty `text`" instead
  of `kind in ("boss", "coder_talk")`, since `kind` is campaign-defined
  rather than a fixed set. `narrate_scene`/`prepare_show` gained required
  `primitives`/`narration_config` parameters. See
  docs/campaign_platform_build.md.
- **v1.3.0** (2026-07-19): New `voice.verbatim` config flag, threaded through
  `prepare_show`/`narrate_scene` as `verbatim=False`. When `True`,
  `boss`/`coder_talk` scenes skip the LLM paraphrase entirely and speak
  the original scripted line in full — no word-count trimming, no
  mid-sentence cutoff on a fallback path. `coder_work` scenes are
  unaffected (always paraphrased — there's no single original line to
  read for a run of tool calls). Default is `False`, so existing configs
  are unchanged.
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
  assign distinct dialogue to up to 6 personas — see
  [duet_replay.md](duet_replay.md)'s "Ownership & uncast-speaker
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
