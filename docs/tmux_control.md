# tmux_control.py

## Overview

The agent's "hands" on its own on-stream tmux UI — the piece
`docs/VTuber_AI_Dev_Team_Concept.md` §13.2 calls out as `act() → writes to
editor pane via tmux send-keys`. Gives the agent loop three primitives:
resolve a pane by name, focus (`select-pane`) it, and type text or full
commands (`send-keys`) into it.

Panes are addressed by their **config id** (`editor`, `filetree`,
`kafka_feed`, `avatar`, `htop` — the same `id`/`use` key from
`config/layouts/*.yaml`), not by tmux's positional pane index. That index
shifts whenever panes are split/reordered (see `build_layout.py`'s
docstring), so it's not a stable target across layout changes.

## Signature

```python
class TmuxError(RuntimeError): ...

def list_panes(session: str = "worker") -> dict[str, str]

def resolve_pane(name: str, session: str = "worker", runtime_dir: str = "/tmp/panes") -> str

def select_pane(name: str, session: str = "worker", runtime_dir: str = "/tmp/panes") -> str

def send_keys(name: str, text: str, enter: bool = True, session: str = "worker", runtime_dir: str = "/tmp/panes") -> str

def send_raw(name: str, *keys: str, session: str = "worker", runtime_dir: str = "/tmp/panes") -> str

def send_command(name: str, command: str, session: str = "worker", runtime_dir: str = "/tmp/panes") -> str
```

## Parameters

- `name` (str) — a config pane id (e.g. `"editor"`), a live tmux pane title
  (e.g. `"Editor"`), or a literal tmux pane_id (e.g. `"%3"`, passed through
  unchanged).
- `session` (str) — the tmux session name; always `"worker"` in this project
  (`build_layout.py`'s `SESSION_NAME`).
- `runtime_dir` (str) — where `build_layout.py` wrote each pane's resolved
  config (`<id>.yaml`, containing its `title`); used to translate a config id
  to the title tmux actually knows about.
- `text` / `command` (str) — what to type into the pane.
- `enter` (bool) — whether to press Enter after typing (submits it as a
  command/line vs. leaving it uncommitted in the pane).
- `*keys` (str, `send_raw`) — one or more tmux key names (e.g. `"Escape"`,
  `"C-c"`, `"Up"`) sent as actual keypresses, not literal text — needed for
  apps like nvim where a bare keypress is a mode switch (`"i"` to enter
  insert mode, `"Escape"` to leave it) rather than text to insert.

## Return Value

- `list_panes` — `{pane_title: tmux_pane_id}` for every pane in the session.
- `resolve_pane` / `select_pane` / `send_keys` / `send_raw` / `send_command`
  — the resolved tmux `pane_id` string (e.g. `"%3"`) the operation acted on.

## Dependencies

- `subprocess` (shells out to the `tmux` CLI already present in the worker image).
- `pyyaml` (reads `build_layout.py`'s resolved per-pane runtime configs).
- Python standard library: `pathlib`.

## Usage Examples

```python
from tmux_control import select_pane, send_keys, send_raw, send_command

select_pane("editor")                              # focus the editor pane
send_command("editor", "git status")                # type + Enter
send_keys("editor", "draft note, not run yet", enter=False)  # type only

# nvim opens in normal mode -- "i" enters insert mode, "Escape" leaves it.
# See agent.py's demo_editor_note for a full worked example.
send_raw("editor", "i")
send_keys("editor", "# TODO: fix the login bug")
send_raw("editor", "Escape")
```

```python
# Resolution also accepts a live tmux title or a literal pane_id directly:
select_pane("Editor")
select_pane("%3")
```

## Error Handling

- Every `tmux` invocation goes through `_run`, which raises `TmuxError` on a
  non-zero exit (stderr included in the message) — callers see a clear
  failure instead of a silently no-op command.
- `resolve_pane` raises `TmuxError` (listing the live pane titles it *did*
  find) when `name` matches neither a live tmux pane_id/title nor a config
  id resolvable via `runtime_dir` — e.g. the layout hasn't been built yet,
  or the name is misspelled.
- `send_keys` always sends text with tmux's `-l` (literal) flag so tmux
  never interprets it as key names — without `-l`, a string that happened to
  contain a token like `"Enter"` could trigger the actual Enter key
  mid-string instead of being typed literally.
- `_pane_titles` skips unreadable/malformed runtime-config files rather than
  failing the whole lookup — one bad pane config shouldn't block resolving
  every other pane.

## Changelog

- v1.0.0 (2026-07-01) — Initial implementation: `list_panes`, `resolve_pane`,
  `select_pane`, `send_keys`, `send_raw`, `send_command`. First consumer:
  `agent.py`'s `demo_editor_note` (see `docs/agent.md`).
