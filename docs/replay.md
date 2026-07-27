# replay

## Overview

Performs a validated episode ([episode_schema.md](episode_schema.md)) as a
paced, colorized "show" on stdout — the reenactment layer of the stream
replay pipeline. Designed to run as a tmux pane command inside a worker
container (the pane simply runs this program), and equally usable in any
terminal for local preview.

**Display-only by design.** Every scene's `render[]` entries are recipes
over a handful of behaviors ([primitives.md](primitives.md)) — they are
*rendered*, never executed. The only side effect outside the show's own
output is the avatar state file (`agent_state.py`), which the existing
avatar pane polls, so the ASCII avatar reacts to the performance (per the
campaign's own `avatar:`/`on_error:` recipe directives).

**Campaign-platform rewrite (2026-07-26).** This module used to own a
fixed set of event handlers keyed on `event["type"]`/`event["tool"]`
(`_on_user_message`, `_perform_shell`, `_perform_edit`, ...), each with its
own hardcoded rates (`DIALOGUE_CPS`, `CODE_CPS`, ...). That's gone: a
scene's `render[]` entries are now performed by handing them to
`app/primitives.py`'s `perform_entry()`, which resolves each entry's
`primitive` name against a campaign's merged recipe table
(`config/primitives.yaml` + `config/campaigns/<campaign>/primitives.yaml`)
and runs its `type`/`print`/`diff`/`image`/`pause` behaviors. This module
now owns only show-level concerns: pacing (`Pacer`), styling (`Palette`),
audio-anchored scene timing, duet hooks, sink routing, and the top-level
`perform()` loop. Adding a new coder-style tool primitive, or a whole new
campaign, is a YAML edit in `config/campaigns/<campaign>/primitives.yaml`,
not a code change here.

**Spoken narration is audio-anchored.** The per-airing narration pass
([revoice.md](revoice.md)) can hand `perform()` a *voiced show* — the
episode's own scenes, each with a spoken line and its synthesized audio
([tts_client.md](tts_client.md)). For each voiced scene the performer:

1. estimates the scene's natural on-screen render time
   (`primitives.estimate_scene_seconds` — sums entries under
   `mode: "sequence"`, maxes them under `"parallel"`),
2. sets a per-scene pacing scale of `natural / audio.duration` (clamped to
   `[0.4, 3.0]`) so typing/scrolling stretches or compresses to the spoken
   line's **measured** duration,
3. starts playback ([audio_player.md](audio_player.md)) and performs the
   scene — then holds the scene until the voice finishes if the visuals
   land first.

So a scene with a long visual sequence plays under continuous narration,
and a short beat doesn't leave the voice talking over the next scene. The
spoken line is also printed (dim, `♪`-prefixed) for muted viewers, and
becomes the avatar's speech bubble. The replayer performs whatever text it
is given and never calls an LLM itself; without a voiced show it performs
exactly as before, silently. This is the ONLY change to `_perform_scene`'s
math versus the pre-campaign-platform version — everything else in that
method (Case A audio-duration / Case B target_duration, the clamp, the
duet ownership handling below) is byte-for-byte unchanged.

**`mode: sequence` vs `mode: parallel`.** A scene's `render[]` entries run
one after another by default (`mode: "sequence"`, or omitted). Setting
`mode: "parallel"` runs every entry concurrently on real background
threads, joined before the scene moves on — so an entry drawing a map on
one pane and a caption typing on another genuinely happen at the same
wall-clock time. `Pacer` is safe to share across those threads: its
`scale`/`speed` are set once per scene, before rendering starts, and
nothing in behavior execution (`sleep`/`type_out`/`check_stop`) mutates
them, only reads. Writes are serialized per-sink (see "Sinks and `target`
routing" below) so two entries can never tear a single `write()` call in
half — but two entries that both target the *same* sink can still
visually interleave line-by-line. That's an honest, deliberate limit
(campaign_platform_contract.md §6 explicitly allows falling back to "a deterministic
interleave" if true concurrency proves unsafe with the shared `Pacer`; in
practice per-write locking made real threads safe enough not to need
that fallback, but the interleaving caveat for same-sink parallel entries
still applies). A `ReplayStopped` raised on any thread (an operator
`replay_stop` firing mid-typing) is captured and re-raised after every
thread is joined, so a stop still unwinds the show even though it fired in
the background. Any OTHER exception from one entry is logged and the rest
of the scene continues — in both `sequence` and `parallel` mode, a single
bad render entry never aborts the scene or the show (the "show must always
air" rule, extended from LLM/TTS failures to rendering bugs).

**Sinks and `target` routing.** Each `render[]` entry's fully-resolved
`target` (entry override > recipe default > `"theater"`, resolved by
`primitives.perform_entry` itself) is routed through `Performer.sinks`, a
`dict[str, TextIO]` defaulting to `{"theater": out}` — i.e. everything
goes to the one output stream a Performer was constructed with, unless the
caller wires up more. **Honest limit**: `app/replay_pane.py` (the
in-container tmux pane program) does not currently construct a
multi-target `sinks` dict — a validator-approved `target` like `"notes"`
that this pane hasn't wired a real sink for falls back to the `"theater"`
sink with a logged `WARNING`, never an error and never invented cross-pane
IPC. Multi-pane routing (e.g. a `"notes"` pane genuinely receiving its own
text) is possible today only for a caller that builds its own `sinks`
dict — see the Usage Examples below — and would need `replay_pane.py`
(or a future pane-aware wrapper) to open real per-pane output streams to
become available end-to-end. The validator's `unknown_target: reject`
rule still catches a genuinely bad target name at ingest; an unresolvable-
but-valid target at *render* time is this degraded case.

**Duet replay hooks.** `Performer.__init__` accepts two optional
keyword-only callbacks, both `None` by default (every existing call site,
and every solo airing, is byte-for-byte unchanged):

- `on_scene_start(scene_index)` — called immediately before performing
  each scene. A duet **director** sets this to publish that scene's
  `replay_cue` to its followers. A raised exception is caught and logged,
  never taking the show down (a bus hiccup shouldn't matter — followers
  recover via their own watchdog).
- `wait_for_scene(scene_index)` — called before each scene (after
  `on_scene_start`, though normally only one of the two is set) and blocks
  until that scene is authorized. A duet **follower** sets this to poll
  its cue file. Returning `J >= scene_index` proceeds (`J - scene_index >=
  2` triggers an unpaced catch-up burst through the backlog); returning
  `-1` ends the show early — an "interrupted" line prints, the avatar
  returns to idle, and `perform()` returns cleanly instead of raising.

Scene dicts also gain two optional keys duet playback reads (on top of the
frozen normalized set — `speaker, kind, text, fallback, mode, render` —
plus `narration`/`audio` from `revoice.prepare_show`, campaign_platform_contract.md §1):

- `"owned"` (bool, default `True` when absent) — gates whether *this*
  worker plays that scene's audio and shows the "speaking" avatar/bubble.
  A scene that isn't owned still renders full visuals and prints the `♪`
  narration line — every cast worker's stream shows the whole episode —
  but sets the avatar to `"idle"` / `"listening to the show"` instead.
- `"target_duration"` (float seconds, optional) — used instead of
  `audio.duration` to scale visual pacing (same `[0.4, 3.0]` clamp) when
  the scene isn't owned, or is owned but has no audio (e.g. a reused
  airing dropped that WAV): the scene holds on the wall clock until
  `target_duration` elapses, keeping this worker's stream in lockstep with
  the scene's owner even with nothing to play back.

Full protocol (director/follower roles, bus message schemas, timeouts,
ownership rules): [docs/duet_replay.md](duet_replay.md).

**Stopping a show early.** `Pacer` accepts an optional `should_stop`
no-arg callable, polled on every sleep and every typed character (not just
between scenes, so an operator stop lands within a fraction of a second
even mid-typing). When it returns `True`, `Pacer.check_stop` raises
`ReplayStopped`, which `perform()` catches at the top level — same clean
shutdown as a duet follower's `wait_for_scene` returning `-1`: a "stopped"
banner prints, the avatar returns to idle, and `perform()` returns `False`
instead of raising. `_perform_scene` also stops any in-flight audio
playback before the exception propagates, so a stopped voiced scene never
leaves narration playing under a show that already ended. `app/replay_pane.py`
wires this to `REPLAY_STOP_FILE` (docs/replay_pane.md), written by
`app/agent.py`'s `handle_replay_stop` on an operator `replay_stop`
(docs/operator_commands.md) — `replay.py` itself has no bus/file
awareness, it just calls whatever `should_stop` it's given.

## Signature

```python
class Performer:
    def __init__(self, out=None, pacer=None, palette=None,
                 worker_name="KODI-7", state_path=None,
                 max_output_lines=24, *,
                 on_scene_start=None, wait_for_scene=None,
                 speaker_names=None, primitives=None, sinks=None)
    def perform(self, episode: dict, show: list[dict] | None = None,
                start: int = 0, limit: int | None = None) -> bool

class Pacer:
    def __init__(self, speed=1.0, enabled=True, should_stop=None)
    def check_stop(self) -> None  # raises ReplayStopped if should_stop() is True

class ReplayStopped(Exception): ...

def load_script(source: str | Path) -> dict   # delegates to episode_schema.load_episode
def prepare_voiced_show(episode, config, workdir, worker_name="KODI-7",
                        speed=1.0, max_output_lines=24,
                        progress=None, campaign="coder") -> list[dict] | None
```

`show` is `revoice.prepare_show()`'s output — the episode's own scenes,
annotated; `start`/`limit` slice the scene list either way, since
`episode['scenes']` is already the unit of performance (no `events[]`
grouping step any more). `prepare_voiced_show` is the config glue: builds
the LLM + TTS clients from a worker config's `llm`/`voice` sections, loads
`campaign`'s primitive recipes + narration config, and runs the narration
pass — returns `None` (silent show) when `voice.provider` is
`null`/missing.

`perform()` is an index-based loop over scenes (not a plain `for`), so a
duet follower's `wait_for_scene` hook can jump the index forward (catch-up
burst) or abort mid-show (`docs/duet_replay.md`). With neither
`on_scene_start` nor `wait_for_scene` set, behavior is identical to a
straight-through loop — solo output is unaffected.

**Deleted** (campaign_platform_contract.md §6 — the per-event/per-tool rendering layer):
`estimate_event_seconds`, `Performer._on_user_message`,
`_on_assistant_text`, `_on_tool_call`, `_perform_shell`, `_perform_edit`,
`_perform_write`, `_perform_read`, `_perform_generic`, `_perform_events`,
and `load_script`'s directory-source branch (`session_log_parser` moved
out to the generator side — `generators/coder/`). The `_typed`/
`_paced_output` helpers those handlers used are gone too, being otherwise
dead. Module-level pacing constants `DIALOGUE_CPS`, `CODE_CPS`,
`OUTPUT_LINES_PER_S`, `EVENT_PAUSE_S`, `TOOL_BEAT_S` are also gone — they
now live as `rate_cps`/`rate_lps`/`seconds` fields in each campaign's own
`config/campaigns/<campaign>/primitives.yaml` recipes. `MAX_OUTPUT_LINES`
(=24) stays as `Performer`'s default `max_output_lines` value, since it's
still a Performer-level default a behavior falls back to when its own
recipe doesn't hardcode a `max_lines`.

## Parameters (CLI)

- `source` (required): episode `.json`, validated via
  `app/episode_schema.py`. No more raw session-log-directory input — that
  moved to the generator side.
- `--speed` (float, default 1.0): playback speed multiplier.
- `--no-delay`: render instantly (testing/preview).
- `--no-color`: disable ANSI colors.
- `--worker-name` (default `KODI-7`): persona name on dialogue lines.
- `--state-file` (default none): avatar state file to drive; in-container
  use `/tmp/agent_state.json` (see `agent_state.py`).
- `--start` / `--limit`: perform a slice of the episode's scenes.
- `--max-output-lines` (default 24): default truncation cap for a
  behavior that doesn't hardcode its own `max_lines` — most shipped coder
  recipes hardcode 24 for fidelity, so this mostly matters for
  shared-layer primitives (`print_text`, `type_text`).
- `--campaign` (default: the episode's own `meta.campaign`, else
  `"coder"`): which campaign's primitive recipes + narration config to
  render/voice with.
- `--voice-config` (default none): a worker config YAML whose `voice` +
  `llm` sections drive spoken narration ([revoice.md](revoice.md)); omit
  for a silent show.

## Return Value

`perform()` returns `True` when the show ran to its natural end, `False`
when it was cut short — either a duet follower's `wait_for_scene` hook
returning `-1` (prints `══ interrupted ══`, avatar -> `idle` "show
interrupted") or an operator `replay_stop` firing `should_stop`
(`ReplayStopped`, prints `══ stopped ══`, avatar -> `idle` "show stopped by
operator"). Both cases return cleanly — never raise — so callers like
`perform_director_request` can tell followers the real reason
(docs/duet_replay.md `replay_end` "finished" vs "stopped"). Interrupting
the CLI with Ctrl-C prints `[replay] interrupted` and exits cleanly
(unrelated to `should_stop`, which the CLI doesn't wire up).

## Dependencies

Standard library plus `app/agent_state.py` (avatar state),
`app/audio_player.py` (playback), and `app/primitives.py`
(`RenderContext`, `perform_entry`, `estimate_scene_seconds` — the
rendering engine and screen-time estimator, frozen per campaign_platform_contract.md §2).
Lazily, when voice is used: `app/revoice.py`, `app/tts_client.py`,
`app/llm_client.py`, `yaml`. Lazily, always, inside `load_script`:
`app/episode_schema.py` (kept lazy so this module stays importable even
without `episode_schema`'s own config dependencies set up).

## Usage Examples

Local preview of an episode, fast:

```bash
python app/replay.py replays/coder/sample.json --speed 4 --no-delay
```

In-container pane command (layout config), driving the avatar:

```yaml
# a layout preset pane entry
- use: editor
  title: "Rerun Theater"
  command: "python3 /app/replay.py /data/replays/coder/episode.json --state-file /tmp/agent_state.json"
```

Building a `Performer` with a second sink for a `target: notes` render
entry (the multi-pane case `replay_pane.py` doesn't wire up today — see
"Sinks and `target` routing" above):

```python
import io
from replay import Pacer, Palette, Performer
from primitives import load_primitives

theater_out, notes_out = open("/tmp/panes/theater", "w"), open("/tmp/panes/notes", "w")
performer = Performer(
    out=theater_out, pacer=Pacer(), palette=Palette(),
    primitives=load_primitives("dnd"),
    sinks={"theater": theater_out, "notes": notes_out},
)
```

## Error Handling

- A `render[]` entry naming an unknown primitive, or otherwise raising,
  is logged (`ERROR`, to stderr — `replay.py`/`replay_pane.py` own stdout
  for the show itself) and the rest of the scene/show continues; this
  should never happen for an episode that passed `episode_schema.py`'s
  `unknown_primitive: reject` at ingest, so it surfacing here means a real
  bug, not a content problem.
- Avatar state write failures are logged to stderr and ignored; the show
  always finishes.
- Legacy Windows consoles (cp1252) are handled by reconfiguring stdout to
  UTF-8 with replacement characters.

Voiced local preview (needs piper + a downloaded voice model, and Ollama
reachable per the config's `llm` section):

```bash
python app/replay.py replays/coder/sample.json --voice-config config/workers/coder.yaml --campaign coder
```

## Changelog

- **v2.0.0** (2026-07-26, campaign-platform build, campaign_platform_contract.md §6):
  **Per-event/per-tool rendering deleted** (`estimate_event_seconds` and
  every `_on_*`/`_perform_*` handler) — replaced by `Performer.
  _perform_render`, which drives a scene's `render[]` entries through
  `app/primitives.py`'s `perform_entry`, honoring `mode: sequence|parallel`
  and routing each entry's resolved `target` through the new `sinks`
  dict (default `{"theater": out}`; an unwired target falls back to
  theater with a logged warning — see "Sinks and `target` routing"
  above). `mode: parallel` runs entries on real background threads,
  joined before the scene continues, with per-sink write locking and
  `ReplayStopped` re-raised after every thread joins if any of them hit
  an operator stop. `Performer.__init__` gained `primitives=None`/
  `sinks=None`. `_perform_scene`'s audio-anchoring math is unchanged
  except that `natural` now comes from `primitives.estimate_scene_seconds`
  instead of summing `estimate_event_seconds` over `scene["events"]` (no
  more `events` key at all — campaign_platform_contract.md §1). `load_script` now delegates
  to `episode_schema.load_episode` and no longer accepts a raw
  session-log directory (`session_log_parser.py` moved out to
  `generators/coder/`). New `--campaign` CLI flag. `speaker_names`/
  `_resolve_display_name`/`_display_name` are kept on `Performer` for
  backward compatibility with the frozen constructor shape, but the
  on-screen speaker label is now baked into a scene's `render[]` payload
  by the generator (e.g. `show_coder_line`'s `payload.name`) rather than
  resolved here at render time. See docs/campaign_platform_build.md.
- **v1.3.0** (2026-07-19): Stoppable shows — `Pacer(should_stop=...)` polled
  on every sleep/typed character, raising the new `ReplayStopped` when it
  fires; `perform()` catches it (same shutdown as the existing
  `wait_for_scene`-abort path) and now returns `bool` (`True` finished,
  `False` cut short) instead of always `None`. `_perform_scene` stops any
  in-flight audio on a mid-scene stop before re-raising. Wired end-to-end
  by the new `replay_stop` operator command (docs/operator_commands.md,
  docs/replay_pane.md). +tests.
- **v1.2.0** (2026-07-13): Duet replay hooks — `Performer.__init__` gained
  keyword-only `on_scene_start`/`wait_for_scene` (both `None` by default,
  every existing caller unaffected); `perform()` became an index-based
  loop so `wait_for_scene` can jump the index forward (fast-forward
  catch-up, `docs/duet_replay.md`) or return `-1` to end the show early.
  Scene dicts gained optional `"owned"` (default `True`) and
  `"target_duration"` keys read by `_perform_scene`'s pacing: an un-owned
  (or owned-but-silent) scene with `target_duration > 0` scales visual
  pacing to it and holds the wall clock instead of playing/anchoring to
  audio, and un-owned scenes show the avatar "listening" instead of
  "speaking". See docs/duet_replay.md.
- **v1.1.0** (2026-07-12): Spoken narration — scene-based `perform(show=)`,
  audio-anchored per-scene pacing (`Pacer.scale`), `estimate_event_seconds`,
  `prepare_voiced_show` config glue, `--voice-config` CLI. +7 tests.
- **v1.0.0** (2026-07-12): Initial version — event rendering (dialogue,
  shell, edit-as-diff, write, read, generic tools), pacing engine, avatar
  integration, truncation, episode slicing. 9 tests.
