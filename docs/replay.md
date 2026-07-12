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

Persona re-voicing (planned, separate pass) rewrites narration text in the
script *before* it reaches this module. The replayer performs whatever text
it is given and never calls an LLM.

## Signature

```python
class Performer:
    def __init__(self, out=None, pacer=None, palette=None,
                 worker_name="KODI-7", state_path=None,
                 max_output_lines=24)
    def perform(self, script: dict, start: int = 0, limit: int | None = None) -> None

def load_script(source: str | Path) -> dict
```

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

## Return Value

None — output is the rendered performance on stdout. Interrupting with
Ctrl-C prints `[replay] interrupted` and exits cleanly.

## Dependencies

Standard library plus `app/agent_state.py` (avatar state) and, only when
`source` is a directory, `app/session_log_parser.py`.

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

## Changelog

- **v1.0.0** (2026-07-12): Initial version — event rendering (dialogue,
  shell, edit-as-diff, write, read, generic tools), pacing engine, avatar
  integration, truncation, episode slicing. 9 tests.
