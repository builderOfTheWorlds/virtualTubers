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

## Signature

```python
class Performer:
    def __init__(self, out=None, pacer=None, palette=None,
                 worker_name="KODI-7", state_path=None,
                 max_output_lines=24)
    def perform(self, script: dict, show: list[dict] | None = None,
                start: int = 0, limit: int | None = None) -> None

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

None — output is the rendered performance on stdout. Interrupting with
Ctrl-C prints `[replay] interrupted` and exits cleanly.

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

- **v1.1.0** (2026-07-12): Spoken narration — scene-based `perform(show=)`,
  audio-anchored per-scene pacing (`Pacer.scale`), `estimate_event_seconds`,
  `prepare_voiced_show` config glue, `--voice-config` CLI. +7 tests.
- **v1.0.0** (2026-07-12): Initial version — event rendering (dialogue,
  shell, edit-as-diff, write, read, generic tools), pacing engine, avatar
  integration, truncation, episode slicing. 9 tests.
