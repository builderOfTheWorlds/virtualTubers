#!/usr/bin/env python3
"""
theme_watcher.py
Headless poll loop that makes a console theme switch (message-api's
POST /console-theme/{worker_id}) take effect on an ALREADY-RUNNING xterm —
no container restart, no stream interruption.

Polls ConsoleThemeControl every POLL_INTERVAL_S; when the resolved theme
name changes, writes an OSC re-theme escape sequence (console_theme.osc_
sequence) directly to the xterm's own tty. Started by startup.sh the same
way replay_pane.py is: unconditionally, off-screen, one per worker
container.

Where the bytes go matters: this process's stdout is captured by
`startup.sh ... &` (no tty), so it writes to a tty directly. The target is
the tmux CLIENT tty (#{client_tty} — the pty xterm allocated for
`-e tmux attach`), not a pane tty: tmux 3.2+ keeps OSC colours sent from a
pane as a per-pane override, so writing to pane 0 re-themed only the
top-left seat of a split layout. Each pane is also sent an OSC reset so
leftover per-pane overrides don't mask the new palette.
"""
import argparse
import logging
import subprocess
import sys
import time

from console_theme import (OSC_RESET_SEQUENCE, ConsoleThemeControl, get_theme,
                            load_themes, osc_sequence, resolve_active_theme_name)
from message_bus import load_worker_config

log = logging.getLogger("theme_watcher")

POLL_INTERVAL_S = 3.0


def _tmux_lines(args):
    """stdout lines of `tmux <args>`, or [] if tmux/the session isn't up
    yet (normal during the boot race; the caller retries)."""
    try:
        out = subprocess.run(["tmux", *args], capture_output=True,
                             text=True, timeout=2)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("tmux %s failed: %s", args[0], exc)
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _client_ttys(session):
    """The tty of every tmux client attached to `session` — i.e. the pty
    xterm allocated for `tmux attach`. Output written here goes straight
    to xterm, so an OSC sequence re-themes the WHOLE window."""
    return _tmux_lines(["list-clients", "-t", session, "-F", "#{client_tty}"])


def _pane_ttys(session):
    """The tty of every pane in `session` (all windows)."""
    return _tmux_lines(["list-panes", "-s", "-t", session, "-F", "#{pane_tty}"])


def _write(tty_path, data):
    try:
        with open(tty_path, "w") as f:
            f.write(data)
        return True
    except OSError as exc:
        log.warning("could not write OSC sequence to %s: %s", tty_path, exc)
        return False


def apply_theme(session, theme):
    """Re-theme the xterm attached to `session`. Returns True if at least
    one client tty took the write.

    Writing into a PANE's tty does not work for a split layout: tmux 3.2+
    turns OSC 10/11/4 from a pane into a colour override for that pane
    alone (the roundtable bug — only pane 0, the top-left seat, changed).
    So the theme goes to the outer xterm via the client tty, and every
    pane gets a reset so stale per-pane overrides stop masking it."""
    for tty in _pane_ttys(session):
        _write(tty, OSC_RESET_SEQUENCE)
    clients = _client_ttys(session)
    if not clients:
        log.debug("no tmux client attached to %r yet", session)
        return False
    ok = False
    for tty in clients:
        ok = _write(tty, osc_sequence(theme)) or ok
    return ok


def run(worker_id, config, session="worker", poll_interval=POLL_INTERVAL_S):
    control = ConsoleThemeControl.from_config(config)
    load_themes()  # fail fast if the theme file is missing/corrupt
    current_name = None
    while True:
        name = resolve_active_theme_name(worker_id, config, control)
        if name != current_name:
            if apply_theme(session, get_theme(name)):
                log.info("theme changed: %r -> %r", current_name, name)
                current_name = name
        time.sleep(poll_interval)


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--worker-id", help="overrides WORKER_ID env / config")
    parser.add_argument("--session", default="worker")
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL_S)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[theme_watcher] %(message)s")

    config = load_worker_config(args.config)
    import os
    worker_id = args.worker_id or os.environ.get("WORKER_ID") \
        or config.get("message_bus", {}).get("worker_id")
    if not worker_id:
        log.error("no worker_id resolved (--worker-id / WORKER_ID / "
                   "message_bus.worker_id) — exiting")
        sys.exit(1)

    run(worker_id, config, session=args.session, poll_interval=args.poll_interval)


if __name__ == "__main__":
    _main()
