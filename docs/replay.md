# replay

## Overview

Performs a parsed session script (see [session_log_parser.md](session_log_parser.md))
as a paced, colorized "show" on stdout — the reenactment layer of the stream
replay pipeline. Designed to run as a tmux pane command inside a worker
container (the pane simply runs this program), and equally usable in any
terminal for local preview.

**Display-only by design.** Recorded commands, edits, and outputs are
*rendered* — never executed. The only side effect is the avatar state file
(`agent_state.py`), which the existing avatar pane polls, so the ASCII
avatar reacts to the performance (thinking on boss messages, speaking
during narration, focused during work, frustrated on recorded failures,
happy at the end).

**Spoken narration is audio-anchored.** The per-airing narration pass
([revoice.md](revoice.md)) can hand `perform()` a *voiced show* — the
script's events grouped into scenes, each with a spoken line and its
synthesized audio ([tts_client.md](tts_client.md)). For each voiced scene
the performer:

1. estimates the scene's natural on-screen render time
   (`estimate_event_seconds` — kept in lockstep with the event handlers'
   pacing math),
2. sets a per-scene pacing scale of `natural / audio.duration` (clamped to
   `[0.4, 3.0]`) so typing/scrolling stretches or compresses to the spoken
   line's **measured** duration,
3. starts playback ([audio_player.md](audio_player.md)) and performs the
   scene — then holds the scene until the voice finishes if the visuals
   land first.

So a scene with minutes of console output plays under continuous narration,
and a short beat doesn't leave the voice talking over the next scene. The
spoken line is also printed (dim, `♪`-prefixed) for muted viewers, and
becomes the avatar's speech bubble. The replayer performs whatever text it
is given and never calls an LLM itself; without a voiced show it performs
exactly as before, silently.

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

Scene dicts also gain two optional keys duet playback reads:

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
                 on_scene_start=None, wait_for_scene=None)
    def perform(self, script: dict, show: list[dict] | None = None,
                start: int = 0, limit: int | None = None) -> bool

class Pacer:
    def __init__(self, speed=1.0, enabled=True, should_stop=None)
    def check_stop(self) -> None  # raises ReplayStopped if should_stop() is True

class ReplayStopped(Exception): ...

def load_script(source: str | Path) -> dict
def estimate_event_seconds(event: dict, max_output_lines=24) -> float
def prepare_voiced_show(script, config, workdir, worker_name="KODI-7",
                        speed=1.0, max_output_lines=24,
                        progress=None) -> list[dict] | None
```

`show` is revoice.prepare_show()'s output; `start`/`limit` slice events
when unvoiced, scenes when voiced. `prepare_voiced_show` is the config
glue: builds the LLM + TTS clients from a worker config's `llm`/`voice`
sections and runs the narration pass — returns None (silent show) when
`voice.provider` is `null`/missing.

`perform()` is an index-based loop over scenes (not a plain `for`), so a
duet follower's `wait_for_scene` hook can jump the index forward (catch-up
burst) or abort mid-show (`docs/duet_replay.md`). With neither
`on_scene_start` nor `wait_for_scene` set, behavior is identical to a
straight-through loop — solo output is unaffected.

## Parameters (CLI)

- `source` (required): script `.json` from `session_log_parser`, **or** a raw
  session log directory (parsed on the fly).
- `--speed` (float, default 1.0): playback speed multiplier.
- `--no-delay`: render instantly (testing/preview).
- `--no-color`: disable ANSI colors.
- `--worker-name` (default `KODI-7`): persona name on dialogue lines.
- `--state-file` (default none): avatar state file to drive; in-container
  use `/tmp/agent_state.json` (see `agent_state.py`).
- `--start` / `--limit`: perform a slice of the episode.
- `--max-output-lines` (default 24): cap on displayed command output /
  file content before truncating with a `(N more lines)` marker.
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

Standard library plus `app/agent_state.py` (avatar state) and
`app/audio_player.py` (playback). Lazily, when voice is used:
`app/revoice.py`, `app/tts_client.py`, `app/llm_client.py`, `yaml`. Only
when `source` is a directory: `app/session_log_parser.py`.

## Usage Examples

Local preview of an episode, fast:

```bash
python app/replay.py path/to/scripts/2026-07-02_04-27-00_6ecdde82.json --speed 4
```

In-container pane command (layout config), driving the avatar:

```yaml
# a layout preset pane entry
- use: editor
  title: "Rerun Theater"
  command: "python3 /app/replay.py /data/replays/episode.json --state-file /tmp/agent_state.json"
```

## Error Handling

- Unknown event types are skipped silently — a script from a newer parser
  never crashes an older replayer.
- Avatar state write failures are logged to stderr and ignored; the show
  always finishes.
- Legacy Windows consoles (cp1252) are handled by reconfiguring stdout to
  UTF-8 with replacement characters.

Voiced local preview (needs piper + a downloaded voice model, and Ollama
reachable per the config's `llm` section):

```bash
python app/replay.py replays/episode.json --voice-config config/workers/coder.yaml
```

## Changelog

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
