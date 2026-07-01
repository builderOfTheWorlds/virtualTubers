#!/usr/bin/env python3
"""
tmux_control.py
The agent's "hands" on its own on-stream tmux UI: select which pane is
focused, and type text/commands into a pane by name — see
docs/VTuber_AI_Dev_Team_Concept.md §13.2.

Panes are addressed by the same `id` (defaults to `use`) that
config/layouts/*.yaml assigns them (e.g. "editor", "filetree"), never by
tmux's positional pane index (0, 1, 2...) — that index shifts whenever
splits are added/reordered (see build_layout.py's docstring). Resolution:

    config id -> title (read from build_layout.py's <runtime-dir>/<id>.yaml)
    -> tmux pane_id (`tmux list-panes -F '#{pane_title}\t#{pane_id}'`)

tmux's own stable pane_id (e.g. "%3") is passed through unchanged if given
directly, and a live pane title matches too.
"""
import subprocess
from pathlib import Path

import yaml

SESSION = "worker"
DEFAULT_RUNTIME_DIR = "/tmp/panes"


class TmuxError(RuntimeError):
    """Raised when a tmux CLI invocation fails, or a pane name can't be resolved."""


def _run(args):
    result = subprocess.run(["tmux"] + args, capture_output=True, text=True)
    if result.returncode != 0:
        raise TmuxError(f"tmux {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _pane_titles(runtime_dir):
    """{config_id: title}, read from the resolved per-pane configs
    build_layout.py writes to `runtime_dir`. Unreadable/malformed files are
    skipped rather than failing the whole lookup."""
    titles = {}
    for path in Path(runtime_dir).glob("*.yaml"):
        try:
            resolved = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        title = resolved.get("title")
        if title:
            titles[resolved.get("id") or path.stem] = title
    return titles


def list_panes(session=SESSION):
    """{pane_title: tmux_pane_id} for every pane currently in `session`."""
    out = _run(["list-panes", "-t", session, "-F", "#{pane_title}\t#{pane_id}"])
    panes = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        title, pane_id = line.split("\t", 1)
        panes[title] = pane_id
    return panes


def resolve_pane(name, session=SESSION, runtime_dir=DEFAULT_RUNTIME_DIR):
    """Resolve a config pane id, a live tmux title, or a literal tmux
    pane_id (e.g. "%3") to a tmux pane_id. Raises TmuxError if nothing matches."""
    if name.startswith("%"):
        return name

    live_panes = list_panes(session)
    if name in live_panes:
        return live_panes[name]

    title = _pane_titles(runtime_dir).get(name)
    if title and title in live_panes:
        return live_panes[title]

    raise TmuxError(
        f"no pane named {name!r} in session {session!r} (live titles: {list(live_panes)})"
    )


def select_pane(name, session=SESSION, runtime_dir=DEFAULT_RUNTIME_DIR):
    """Focus the named pane, i.e. change which pane is visually 'active' on stream."""
    pane_id = resolve_pane(name, session, runtime_dir)
    _run(["select-pane", "-t", pane_id])
    return pane_id


def send_keys(name, text, enter=True, session=SESSION, runtime_dir=DEFAULT_RUNTIME_DIR):
    """Type `text` into the named pane.

    Sent with tmux's `-l` (literal) flag so tmux never interprets it as key
    names — without `-l`, a narration string that happened to contain a
    token like "Enter" could trigger the actual Enter key mid-string instead
    of being typed as text. `enter=True` (default) presses Enter afterward,
    submitting it as a command/line; `enter=False` leaves it sitting
    uncommitted in the pane (e.g. to build up input across multiple calls).
    """
    pane_id = resolve_pane(name, session, runtime_dir)
    if text:
        _run(["send-keys", "-t", pane_id, "-l", "--", text])
    if enter:
        _run(["send-keys", "-t", pane_id, "Enter"])
    return pane_id


def send_raw(name, *keys, session=SESSION, runtime_dir=DEFAULT_RUNTIME_DIR):
    """Send one or more tmux key names (e.g. "Escape", "C-c", "Up") to the
    named pane — NOT literal text; use `send_keys` for that. Needed for
    apps like nvim where a bare keypress (e.g. "i" to enter insert mode,
    "Escape" to leave it) is a mode switch, not text to insert."""
    pane_id = resolve_pane(name, session, runtime_dir)
    _run(["send-keys", "-t", pane_id, *keys])
    return pane_id


def send_command(name, command, session=SESSION, runtime_dir=DEFAULT_RUNTIME_DIR):
    """Type `command` into the named pane and press Enter to run it."""
    return send_keys(name, command, enter=True, session=session, runtime_dir=runtime_dir)
