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

Writing to /proc/<xterm_pid>/fd/0 rather than this process's own stdout:
this process's stdout is captured by `startup.sh ... &` (backgrounded, no
tty of its own), but xterm reads OSC sequences off ITS controlling
terminal's input, which is the pty it allocated for the tmux session it is
running (`-e tmux attach`). Writing to the pane's own devtty (found via
tmux's #{pane_tty}, same lookup pane_geometry.py already relies on for
window geometry) reaches that reliably without assuming a fixed fd path
across container base images.
"""
import argparse
import logging
import subprocess
import sys
import time

from console_theme import (ConsoleThemeControl, get_theme, load_themes,
                            osc_sequence, resolve_active_theme_name)
from message_bus import load_worker_config

log = logging.getLogger("theme_watcher")

POLL_INTERVAL_S = 3.0


def _pane_tty(session):
    """The devtty of tmux session `session`'s first pane — the terminal
    xterm itself is attached to. None if tmux/the session isn't up yet
    (normal during the boot race; the caller retries)."""
    try:
        out = subprocess.run(
            ["tmux", "display", "-p", "-t", f"{session}:0.0", "#{pane_tty}"],
            capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("tmux display failed: %s", exc)
        return None
    tty = out.stdout.strip()
    return tty or None


def _write_osc(tty_path, theme):
    try:
        with open(tty_path, "w") as f:
            f.write(osc_sequence(theme))
    except OSError as exc:
        log.warning("could not write OSC sequence to %s: %s", tty_path, exc)


def run(worker_id, config, session="worker", poll_interval=POLL_INTERVAL_S):
    control = ConsoleThemeControl.from_config(config)
    load_themes()  # fail fast if the theme file is missing/corrupt
    current_name = None
    while True:
        tty = _pane_tty(session)
        if tty:
            name = resolve_active_theme_name(worker_id, config, control)
            if name != current_name:
                log.info("theme changed: %r -> %r", current_name, name)
                _write_osc(tty, get_theme(name))
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
