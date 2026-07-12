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

The pane produces to Kafka (never consumes): after a voiced airing it
publishes the spoken transcript as a replay_narration message so
message-logger persists it to Postgres's voiced_narration table — the
synthesized audio itself is regenerated fresh every airing and never
saved, so this is the only durable record of what was said (see
docs/revoice.md).
"""
import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from agent_state import resolve_state_path
from message_bus import MessageProducer, build_message
from replay import Pacer, Palette, Performer, load_script, prepare_voiced_show

DEFAULT_LIBRARY = "/data/replays"
DEFAULT_REQUEST_FILE = "/tmp/replay_request.json"
DEFAULT_WORKER_CONFIG = "/config/worker.yaml"
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


def prepare_voice(script, config, workdir, worker_name, speed):
    """Best-effort per-airing narration pass (docs/revoice.md). Returns a
    voiced show, or None for a silent performance — voice being disabled,
    unconfigured, or broken must never stop an episode from airing."""
    if not config:
        return None
    try:
        show = prepare_voiced_show(
            script, config, workdir, worker_name=worker_name, speed=speed,
            progress=lambda message: print(f"[replay_pane] preparing: {message}"),
        )
    except Exception as exc:
        print(f"[replay_pane] voice preparation failed ({exc}) — silent show",
              file=sys.stderr)
        return None
    if show is not None:
        voiced = sum(1 for scene in show if scene.get("audio"))
        print(f"[replay_pane] tonight's episode: {len(show)} scenes, {voiced} voiced")
    return show


def publish_narration(show, config, episode, worker_name):
    """Best-effort: publish this airing's spoken transcript (text only, no
    audio) onto the bus so message-logger persists it to Postgres's
    voiced_narration table. The synthesized WAVs never leave the temp
    workdir cleaned up around prepare_voice/perform — this is the only
    durable record of what got said. Kafka being down/unconfigured must
    never stop or delay a show, so this always runs after the show is
    already fully prepared and never raises."""
    if not show:
        return
    bus_config = (config or {}).get("message_bus") or {}
    bootstrap_servers = bus_config.get("bootstrap_servers")
    topic = bus_config.get("topic")
    if not bootstrap_servers or not topic:
        return
    payload = {
        "episode": episode,
        "aired_at": datetime.now(timezone.utc).isoformat(),
        "scenes": [
            {
                "index": index,
                "kind": scene.get("kind"),
                "speaker": scene.get("speaker"),
                "text": scene.get("narration"),
            }
            for index, scene in enumerate(show)
        ],
    }
    worker_id = bus_config.get("worker_id", worker_name)
    try:
        MessageProducer(bootstrap_servers, topic).send(
            build_message(worker_id, "broadcast", "replay_narration", payload)
        )
    except Exception as exc:
        print(f"[replay_pane] narration transcript publish failed: {exc}", file=sys.stderr)


def perform_request(request, library, worker_name, state_path, default_speed=1.0,
                    config=None):
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
    name = str(request.get("worker_name") or worker_name)
    performer = Performer(
        pacer=Pacer(speed=speed),
        palette=Palette(enabled=True),
        worker_name=name,
        state_path=state_path,
    )
    with tempfile.TemporaryDirectory(prefix="replay_voice_") as workdir:
        show = None
        if request.get("voice") is not False:  # request can force a silent airing
            show = prepare_voice(script, config, workdir, name, speed)
            publish_narration(show, config, episode, name)
        performer.perform(script, show=show)
    return True


def load_worker_config(path):
    """Worker config for the voice/llm sections; None (silent shows) when
    missing or unparseable."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or None
    except Exception as exc:
        print(f"[replay_pane] could not read worker config {path}: {exc}",
              file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="Rerun Theater pane — idles, performs requested episodes")
    parser.add_argument("--library", default=os.environ.get("REPLAY_LIBRARY", DEFAULT_LIBRARY))
    parser.add_argument("--request-file", default=os.environ.get("REPLAY_REQUEST_FILE", DEFAULT_REQUEST_FILE))
    parser.add_argument("--worker-name", default=os.environ.get("WORKER_ID", "worker"))
    parser.add_argument("--config", default=os.environ.get("CONFIG_PATH", DEFAULT_WORKER_CONFIG),
                        help="Worker config YAML — its voice+llm sections drive spoken "
                             "narration (voice.provider: null keeps shows silent)")
    parser.add_argument("--once", action="store_true",
                        help="Handle at most one pending request, then exit (testing)")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    state_path = resolve_state_path()
    config = load_worker_config(args.config)
    provider = ((config or {}).get("voice") or {}).get("provider")
    print(f"[replay_pane] library={args.library} request_file={args.request_file} "
          f"voice={'on' if provider not in (None, 'null') else 'off'}")

    if args.once:
        request = read_request(args.request_file)
        if request:
            perform_request(request, args.library, args.worker_name, state_path,
                            config=config)
        return

    last_drawn = 0.0
    while True:
        request = read_request(args.request_file)
        if request:
            try:
                perform_request(request, args.library, args.worker_name, state_path,
                                config=config)
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
