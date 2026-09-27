#!/usr/bin/env python3
"""
music_director.py — the standalone live music process for the roundtable.

    python3 music_director.py --theme /campaigns/ashiorid/music/theme.yaml \
        --worker-id roundtable --sink music

Loop: resolve mood (music/control.py: GM override in Redis > scene cue file
> hold) -> render one bar (music/engine.py) -> hand it to a writer thread
that streams s16le PCM into `pacat` on the `music` Pulse sink -> hand it
to the recorder (music/recorder.py) which writes it to Postgres.

pacat's blocking write paces the loop in real time; the writer queue holds
one bar of look-ahead so a slow render never under-runs the sink. A mood
change is therefore heard within ~1-2 bars, on a bar line.

Ducking under speech is done downstream in ffmpeg (stream_supervisor.py
sidechaincompress keyed on the voice sink), so this process never needs to
know when anybody is talking.

Failure policy: Redis down -> follow scene cues; Postgres down -> play
unrecorded; pacat dies -> restart it. The music must keep going.
"""
import argparse
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time

import numpy as np

from music.control import (SILENCE, ControlSources, MoodResolver, MusicControl,
                           DEFAULT_CUE_PATH)
from music.engine import MusicEngine
from music.theme import load_theme

log = logging.getLogger("music_director")

FADE_BARS = 2


class PacatWriter(threading.Thread):
    def __init__(self, sink, sample_rate, lookahead=1):
        super().__init__(name="music-pacat", daemon=True)
        self.sink = sink
        self.sr = sample_rate
        self.queue = queue.Queue(maxsize=lookahead)
        self._proc = None
        self._stop = threading.Event()

    def _spawn(self):
        cmd = ["pacat", "--playback", "--raw", "--format=s16le", f"--rate={self.sr}",
               "--channels=2", "--latency-msec=400", "--client-name=music_director",
               "--stream-name=roundtable-music"]
        if self.sink:
            cmd.append(f"--device={self.sink}")
        log.debug("spawning %s", cmd)
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def run(self):
        while not self._stop.is_set():
            item = self.queue.get()
            if item is None:
                break
            pcm = (np.clip(item, -1, 1) * 32767).astype("<i2").tobytes()
            for attempt in range(3):
                try:
                    if self._proc is None or self._proc.poll() is not None:
                        self._spawn()
                    proc = self._proc
                    assert proc is not None and proc.stdin is not None
                    proc.stdin.write(pcm)
                    proc.stdin.flush()
                    break
                except (OSError, BrokenPipeError) as exc:
                    log.error("pacat write failed attempt=%d err=%s", attempt + 1, exc)
                    self._proc = None
                    time.sleep(0.5)
        self.close()

    def put(self, audio):
        self.queue.put(audio)

    def close(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                self._proc.kill()


def _make_redis(url):
    if not url:
        return None
    try:
        import redis
        return redis.Redis.from_url(url, socket_timeout=1, decode_responses=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("redis unavailable url=%s err=%s — scene cues only", url, exc)
        return None


def resolve_theme_path(themes_dir, theme_name):
    """<themes_dir>/<theme_name>/music/theme.yaml, or None if the name is
    unsafe or the file is missing."""
    import re
    from pathlib import Path
    if not themes_dir or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", theme_name or ""):
        return None
    path = Path(themes_dir) / theme_name / "music" / "theme.yaml"
    return path if path.is_file() else None


class ThemeSession:
    """The engine + recorder for ONE theme. A scene cue naming another
    campaign swaps the whole session (new Postgres music_sessions row), so
    each recorded session is exactly one theme."""

    def __init__(self, args, theme, mood, intensity=0.5, campaign=""):
        self.theme = theme
        self.engine = MusicEngine(theme, mood=mood, intensity=intensity,
                                  transition_bars=args.transition_bars)
        args_campaign = campaign or args.campaign or theme.name
        self.recorder = _make_recorder(args, self.engine, campaign=args_campaign)

    def close(self):
        if self.recorder:
            self.recorder.close()


def _make_recorder(args, engine, campaign=""):
    if args.no_record:
        return None
    try:
        from music import music_store
        from music.recorder import MusicRecorder
        if not music_store.available():
            log.warning("postgres not configured — music will NOT be recorded")
            return None
        rec = MusicRecorder(music_store, engine, worker_id=args.worker_id,
                            campaign=campaign or engine.theme.name,
                            audio_format=args.audio_format,
                            max_segment_s=args.segment_seconds)
        rec.start()
        return rec
    except Exception as exc:  # noqa: BLE001 — play unrecorded
        log.error("music recorder start failed err=%s — playing unrecorded", exc)
        return None


def _initial_theme(args):
    if args.theme:
        return load_theme(args.theme)
    path = resolve_theme_path(args.themes_dir, args.theme_name)
    if path is None:
        raise SystemExit(f"no theme: pass --theme, or --themes-dir with a "
                         f"{args.theme_name}/music/theme.yaml in it")
    return load_theme(path)


def run(args):
    session = ThemeSession(args, _initial_theme(args), args.initial_mood)
    sources = ControlSources(args.worker_id, cue_path=args.cue_path,
                             redis_client=_make_redis(args.redis_url))
    resolver = MoodResolver(min_dwell_s=args.min_dwell_s)
    writer = PacatWriter(args.sink, session.engine.sr)
    writer.start()

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    gain = 0.0  # fade in over FADE_BARS
    pending_theme = None  # theme name waiting for the fade-out to finish
    status_ctl = MusicControl(sources.redis) if sources.redis is not None else None
    log.info("music director started theme=%s worker=%s sink=%s recorder=%s",
             session.theme.name, args.worker_id, args.sink,
             session.recorder.session_id if session.recorder else None)
    try:
        while not stop.is_set():
            scene = sources.read_scene()
            req = resolver.resolve(sources.read_override(), scene)

            # Theme switch requested by a scene cue: fade out, swap, fade in.
            if (scene is not None and scene.theme and scene.theme != session.theme.name
                    and pending_theme != scene.theme):
                if resolve_theme_path(args.themes_dir, scene.theme):
                    log.info("music theme switch requested from=%s to=%s",
                             session.theme.name, scene.theme)
                    pending_theme = scene.theme
                else:
                    log.warning("music theme %r not found under %s — keeping %s",
                                scene.theme, args.themes_dir, session.theme.name)
                    pending_theme = None
            if pending_theme and gain <= 0.0:
                try:
                    new_theme = load_theme(resolve_theme_path(args.themes_dir, pending_theme))
                    session.close()
                    session = ThemeSession(args, new_theme,
                                           req.mood if req.mood != SILENCE else "neutral",
                                           req.intensity, campaign=pending_theme)
                except Exception as exc:  # noqa: BLE001 — keep the old theme playing
                    log.error("music theme switch failed theme=%s err=%s", pending_theme, exc)
                pending_theme = None

            engine, recorder = session.engine, session.recorder
            silent = req.mood == SILENCE or pending_theme is not None
            if req.mood != SILENCE:
                engine.set_mood(req.mood, req.intensity)
                if recorder:
                    recorder.set_context(req.source, req.scene_id)
            bar = engine.render_bar()
            audio = bar.audio if bar.audio is not None else np.zeros((0, 2), np.float32)
            target_gain = 0.0 if silent else args.volume
            start_gain = gain
            step = (args.volume / FADE_BARS)
            gain = (max(target_gain, gain - step) if target_gain < gain
                    else min(target_gain, gain + step))
            ramp = np.linspace(start_gain, gain, audio.shape[0], dtype=np.float32)[:, None]
            bar.audio = audio * ramp
            if recorder and (gain > 0 or start_gain > 0):
                recorder.add_bar(bar)
            if status_ctl is not None:
                status_ctl.publish_status(args.worker_id, {
                    "theme": session.theme.name, "mood": req.mood, "intensity": req.intensity,
                    "source": req.source, "scene_id": req.scene_id,
                    "playing_mood": bar.mood, "tempo_bpm": round(bar.params.tempo_bpm, 1),
                    "mode": bar.params.mode, "bar": bar.index, "gain": round(gain, 3),
                    "recording_session": recorder.session_id if recorder else None,
                    "at": time.time(),
                })
            writer.put(bar.audio)
    finally:
        log.info("music director stopping")
        session.close()
        writer.put(None)
        writer.join(timeout=10)
    return 0


def music_config(config):
    """The worker config's `music:` section merged over env overrides.
    MUSIC_ENABLED env (1/true/0/false) beats `music.enabled`; default off."""
    section = dict((config or {}).get("music") or {})
    env = os.environ.get("MUSIC_ENABLED")
    if env is not None and env.strip() != "":
        section["enabled"] = env.strip().lower() in ("1", "true", "yes", "on")
    section.setdefault("enabled", False)
    return section


def main(argv=None):
    logging.basicConfig(level=os.environ.get("MUSIC_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None,
                    help="worker YAML; its `music:` section supplies defaults")
    ap.add_argument("--print-enabled", action="store_true",
                    help="print 1/0 for whether music is enabled for this worker and exit "
                         "(startup.sh gate)")
    ap.add_argument("--theme", default=os.environ.get("MUSIC_THEME_PATH", ""),
                    help="explicit theme YAML (overrides --themes-dir/--theme-name)")
    ap.add_argument("--themes-dir", default=os.environ.get("MUSIC_THEMES_DIR", "/data/campaigns"),
                    help="campaigns root; themes live at <dir>/<campaign>/music/theme.yaml")
    ap.add_argument("--theme-name", default=os.environ.get("MUSIC_DEFAULT_THEME", "ashiorid"),
                    help="campaign theme to play until a scene cue names another")
    ap.add_argument("--worker-id", default=os.environ.get("WORKER_ID", "roundtable"))
    ap.add_argument("--campaign", default="")
    ap.add_argument("--sink", default=os.environ.get("MUSIC_SINK", "music"))
    ap.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://redis:6379"))
    ap.add_argument("--cue-path", default=os.environ.get("MUSIC_CUE_PATH", DEFAULT_CUE_PATH))
    ap.add_argument("--initial-mood", default="peacefulness")
    ap.add_argument("--volume", type=float, default=float(os.environ.get("MUSIC_VOLUME", "0.8")))
    ap.add_argument("--min-dwell-s", type=float, default=20.0)
    ap.add_argument("--transition-bars", type=int, default=2)
    ap.add_argument("--segment-seconds", type=float, default=60.0)
    ap.add_argument("--audio-format", choices=("opus", "wav"), default="opus")
    ap.add_argument("--no-record", action="store_true")

    # Config `music:` section -> argparse defaults (CLI flags still win; env
    # vars already baked into the defaults above win over config).
    pre, _ = ap.parse_known_args(argv)
    config = {}
    if pre.config:
        import yaml
        try:
            with open(pre.config, "r", encoding="utf-8") as fh:
                config = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error("music config unreadable path=%s err=%s", pre.config, exc)
    section = music_config(config)
    if pre.print_enabled:
        print("1" if section.get("enabled") else "0")
        return 0
    env_backed = {"theme_name": "MUSIC_DEFAULT_THEME", "themes_dir": "MUSIC_THEMES_DIR",
                  "volume": "MUSIC_VOLUME", "sink": "MUSIC_SINK"}
    defaults = {}
    for key in ("theme_name", "themes_dir", "initial_mood", "volume", "min_dwell_s",
                "transition_bars", "segment_seconds", "audio_format", "sink"):
        if key in section and not os.environ.get(env_backed.get(key, "")):
            defaults[key] = section[key]
    if section.get("record") is False:
        defaults["no_record"] = True
    ap.set_defaults(**defaults)
    args = ap.parse_args(argv)
    log.info("music director config enabled=%s theme_name=%s themes_dir=%s volume=%s record=%s",
             section.get("enabled"), args.theme_name, args.themes_dir, args.volume,
             not args.no_record)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
