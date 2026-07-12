#!/usr/bin/env python3
"""
replay_pane.py
Long-lived tmux pane program for "Rerun Theater": idles with an episode
listing, and performs an episode (app/replay.py) whenever the agent drops
a request file.

The request file is the agent -> pane handoff (same local-file IPC pattern
as agent_state.py): agent.py's replay_request handler writes
REPLAY_REQUEST_FILE atomically; this pane polls for it, performs the
episode, deletes the file, and returns to the idle screen. File-based on
purpose — the pane never consumes Kafka and never executes anything from
the bus; the only thing a bus message can influence is WHICH pre-built,
pre-redacted episode in the library gets played.

Episode names are resolved strictly to basenames inside REPLAY_LIBRARY, so
a hostile payload can't traverse to arbitrary files.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from agent_state import resolve_state_path
from replay import Pacer, Palette, Performer, load_script

DEFAULT_LIBRARY = "/data/replays"
DEFAULT_REQUEST_FILE = "/tmp/replay_request.json"
POLL_INTERVAL_S = 2.0
IDLE_REDRAW_S = 300  # re-list the library occasionally (new episodes synced in)


def resolve_episode(library, episode):
    """Map a requested episode name to a file inside the library.

    Basename-only (no traversal), '.json' optional, and a raw session
    directory of the same name is accepted too. Returns None when nothing
    matches — the caller reports, never raises.
    """
    if not episode:
        return None
    name = Path(str(episode)).name  # strips any path components
    library = Path(library)
    for candidate in (library / name, library / f"{name}.json"):
        if candidate.exists():
            return candidate
    return None


def read_request(request_file):
    """Read-and-consume the request file. Returns the request dict or None.
    A malformed file is consumed (deleted) and reported — a bad request must
    not wedge the pane in a crash loop."""
    path = Path(request_file)
    if not path.exists():
        return None
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError(f"expected object, got {type(request).__name__}")
    except (OSError, ValueError) as exc:
        print(f"[replay_pane] discarding malformed request: {exc}", file=sys.stderr)
        request = None
    try:
        path.unlink()
    except OSError:
        pass
    return request


def list_episodes(library):
    library = Path(library)
    if not library.is_dir():
        return []
    return sorted(p.stem for p in library.glob("*.json"))


def draw_idle_screen(library, worker_name):
    episodes = list_episodes(library)
    print("\x1b[2J\x1b[H", end="")  # clear pane between shows
    print("╔══════════════════════════════════════╗")
    print("║          R E R U N   T H E A T E R   ║")
    print("╚══════════════════════════════════════╝")
    print(f" host: {worker_name}")
    if episodes:
        print(f" {len(episodes)} episode(s) in the library:")
        for name in episodes[:20]:
            print(f"   • {name}")
        if len(episodes) > 20:
            print(f"   … and {len(episodes) - 20} more")
    else:
        print(f" library empty ({library}) — sync episode scripts to the host")
    print()
    print(' waiting for a replay_request ("perform episode X")…')


def perform_request(request, library, worker_name, state_path, default_speed=1.0):
    """Resolve and perform one request. Returns True if an episode played."""
    episode = request.get("episode")
    source = resolve_episode(library, episode)
    if source is None:
        print(f"[replay_pane] episode not found in {library}: {episode!r}", file=sys.stderr)
        return False
    try:
        speed = float(request.get("speed") or default_speed)
    except (TypeError, ValueError):
        speed = default_speed
    script = load_script(source)
    performer = Performer(
        pacer=Pacer(speed=speed),
        palette=Palette(enabled=True),
        worker_name=str(request.get("worker_name") or worker_name),
        state_path=state_path,
    )
    performer.perform(script)
    return True


def main():
    parser = argparse.ArgumentParser(description="Rerun Theater pane — idles, performs requested episodes")
    parser.add_argument("--library", default=os.environ.get("REPLAY_LIBRARY", DEFAULT_LIBRARY))
    parser.add_argument("--request-file", default=os.environ.get("REPLAY_REQUEST_FILE", DEFAULT_REQUEST_FILE))
    parser.add_argument("--worker-name", default=os.environ.get("WORKER_ID", "worker"))
    parser.add_argument("--once", action="store_true",
                        help="Handle at most one pending request, then exit (testing)")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    state_path = resolve_state_path()
    print(f"[replay_pane] library={args.library} request_file={args.request_file}")

    if args.once:
        request = read_request(args.request_file)
        if request:
            perform_request(request, args.library, args.worker_name, state_path)
        return

    last_drawn = 0.0
    while True:
        request = read_request(args.request_file)
        if request:
            try:
                perform_request(request, args.library, args.worker_name, state_path)
            except Exception as exc:  # one bad episode must not kill the pane
                print(f"[replay_pane] episode failed: {exc}", file=sys.stderr)
            time.sleep(5)  # hold the final frame briefly
            last_drawn = 0.0  # force idle redraw
        if time.time() - last_drawn > IDLE_REDRAW_S:
            draw_idle_screen(args.library, args.worker_name)
            last_drawn = time.time()
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
