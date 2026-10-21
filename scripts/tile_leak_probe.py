#!/usr/bin/env python3
"""
tile_leak_probe.py — offline memory-growth probe for a roundtable tile.

WHY. Live roundtable tile processes (`tile_pane.py --slot tuber_N`) grew to
4-10 GiB RSS over ~3.8 days. The office go-live runs 7+ days, so any
per-frame / per-line state that accumulates is fatal. This drives one tile's
hot paths in-process, as fast as the CPU allows, against a SIMULATED clock,
and samples RSS (this process + its children, i.e. the spawned GPU render
worker) so growth can be read off as MB per simulated hour.

WHAT IT DRIVES (the same code the live tile runs; only sleeps, the audio
player and the GPU-less bits are faked, and each is noted below):
  * avatar : TileAvatar -> CodecAvatarProvider.render_tick -> GPURenderWorker
             subprocess (gl_raster/EGL) -> pygame blit -> window_shape SHAPE
             mask. Needs an X display (run under Xvfb) and pygame/moderngl,
             i.e. the vtube-worker image, not the bare .venv.
  * text   : TileRenderer + render_tile + gaze.write_stage +
             audio_envelope.compute_envelope per spoken line, with fake relay
             files in a temp dir.
  * live    : live_pane.perform_live_line — the REAL per-line path of a live
             roundtable tile (Performer + TileRenderer + voice gate + stage
             writer + WAV envelope); Performer pacing is sped up and `paplay` is
             replaced by a no-op child process.
  * all    : avatar + live, interleaved (head frames per spoken line), the
             head ticked from the MAIN thread.
  * threaded : the live tile's real thread layout: the head runs in
             tile_pane.TileAvatarDriver's own thread (paced, with
             StageGaze) while the main thread performs live lines.
             Mix of frames:lines is accelerated vs. air; use avatar-only and
             live-only for per-frame / per-line slopes.

Usage. The head needs pygame + moderngl + an X display, i.e. the
vtube-worker image (the bare .venv has neither); `text`/`live` modes run in
the .venv. In the image, without touching any live container:

    docker run --rm --network none --gpus all -v $PWD:/src:ro \
      --entrypoint sh vtube-worker:latest -c '
        cp -r /src/app /src/config /src/scripts /tmp/; cd /tmp
        Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX & sleep 2
        export DISPLAY=:99
        xterm -geometry 320x82+0+0 & sleep 2      # the live head sits over an xterm
        python3 scripts/tile_leak_probe.py --mode threaded --lines 4000 \
            --line-wall-s 0.25 --samples 16 --json /tmp/out.json'

(Keep the xterm: the leak this found only shows with the SHAPE-clipped head
flipping over a real window.)

Simulated time: avatar frames are 1/30 s each (TILE_AVATAR_FPS), so
`--mode avatar --lines 2000 --frames-per-line 27` is 54,000 frames = 30
simulated minutes at 30 fps. In `threaded` mode the clock is the number of
frames the driver thread actually rendered. The summary scales the measured
least-squares RSS slope to MB per simulated hour and to 8 days.
"""
import argparse
import gc
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import tracemalloc
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

SIM_FPS = 30.0


# ── RSS sampling ─────────────────────────────────────────────────────────────
def _rss_mb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def _children(pid):
    out = []
    try:
        for task in os.listdir(f"/proc/{pid}/task"):
            try:
                with open(f"/proc/{pid}/task/{task}/children") as f:
                    out.extend(int(x) for x in f.read().split())
            except OSError:
                pass
    except OSError:
        pass
    return out


def sample_rss():
    """(self_mb, children_mb_total)."""
    me = os.getpid()
    kids = _children(me)
    return _rss_mb(me), sum(_rss_mb(k) for k in kids)


# ── fixtures ─────────────────────────────────────────────────────────────────
def make_wav(path, seconds=6.0, rate=22050):
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        import math
        frames = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 180 * i / rate)
                                  * (0.5 + 0.5 * math.sin(2 * math.pi * 3 * i / rate))))
            for i in range(n))
        w.writeframes(frames)


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


SLOTS = [f"tuber_{i}" for i in range(8)]
LINE_TEXT = ("Well now, that is quite a development, {n}. I did not expect the "
             "ledger to balance after all of that, but here we are, standing in "
             "the lamplight and wondering what the morning will bring.")


class Probe:
    def __init__(self, args):
        self.args = args
        self.tmp = Path(tempfile.mkdtemp(prefix="tile_leak_probe_"))
        self.slot = args.slot
        self.clock = FakeClock()
        self.samples = []
        self.t0 = time.monotonic()
        self.frames = 0
        self.lines_done = 0
        self.avatar = None
        self.renderer = None
        self.stage_path = str(self.tmp / "stage.json")
        self.state_path = str(self.tmp / f"{self.slot}.state.json")
        self.wav = self.tmp / "line.wav"
        make_wav(self.wav)

        if args.mode in ("avatar", "all"):
            self._build_avatar()
        if args.mode in ("text", "live", "all", "threaded"):
            self._build_text()
        if args.mode in ("live", "all", "threaded"):
            self._build_live()
        if args.mode == "threaded":
            self._build_threaded()

    # -- builders ------------------------------------------------------------
    def _build_avatar(self):
        import gaze
        from tile_avatar import make_tile_avatar
        import yaml
        cfg = yaml.safe_load(open(ROOT / "config/workers/roundtable.yaml"))
        self.avatar = make_tile_avatar(cfg, self.slot, (0, 0, 480, 540))
        if self.avatar is None or not self.avatar.active:
            raise SystemExit("probe: 3D head did not start (need DISPLAY + pygame)")
        # Same glue the live tile uses, on the simulated clock.
        self.stage_gaze = gaze.StageGaze(self.slot, self.stage_path,
                                         clock=self.clock, mono=self.clock)

    def _build_text(self):
        import tile_pane
        self.tile_pane = tile_pane
        self.sink = io.StringIO()
        self.renderer = tile_pane.TileRenderer(self.slot, self.state_path,
                                               out=self.sink, fade_enabled=False)

    def _build_live(self):
        """Wire live_pane's real tile-side path to temp dirs and make the
        Performer's typing/sleeping fast (it paces to real time otherwise)."""
        import replay
        import yaml
        self.live_pane = __import__("live_pane")
        self.config = yaml.safe_load(open(ROOT / "config/workers/roundtable.yaml"))
        self.relay_dir = str(self.tmp / "relay")
        os.makedirs(self.relay_dir, exist_ok=True)
        os.environ.setdefault("REPLAY_STOP_FILE", str(self.tmp / "no-stop"))
        os.environ["VOICE_GATE_DIR"] = str(self.tmp / "gate")
        real_pacer = replay.Pacer
        speed = self.args.pace_speed

        class FastPacer(real_pacer):
            def __init__(self, *a, **kw):
                kw["speed"] = speed
                super().__init__(**kw)
        replay.Pacer = FastPacer
        # No sound device here: spawn a no-op child instead of paplay/ffplay
        # (which would play the whole 6 s WAV for real). Still exercises
        # Popen + reaping per line like audio_player.play_wav.
        import subprocess
        from audio_player import Playback

        def fake_play_wav(path, out=sys.stderr):
            return Playback(subprocess.Popen(
                ["true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        replay.play_wav = fake_play_wav
        # Share ONE renderer sink so the tile's pty output does not pile up.
        self.live_out = io.StringIO()

    def _build_threaded(self):
        import gaze
        import tile_avatar
        import tile_pane
        import yaml
        self.ticks = 0
        probe = self
        cfg = self.config
        stage_path = os.path.join(self.relay_dir, "stage.json")
        slot = self.slot

        def builder():
            av = tile_avatar.make_tile_avatar(cfg, slot, (0, 0, 480, 540))
            real_tick = av.tick

            def counting_tick(*a, **kw):
                ok = real_tick(*a, **kw)
                probe.ticks += 1
                return ok
            av.tick = counting_tick
            return av
        self.driver = tile_pane.TileAvatarDriver(
            builder, gaze_source=gaze.StageGaze(slot, stage_path).sample,
            fps=self.args.driver_fps)
        self.driver.start()
        deadline = time.monotonic() + 60
        while self.ticks == 0 and time.monotonic() < deadline:
            time.sleep(0.2)
        if self.ticks == 0:
            raise SystemExit("probe: threaded head never ticked (need DISPLAY)")

    def _live_line(self, n, speaker):
        payload = {
            "type": "live_line", "line_id": f"line-{n}", "speaker": self.slot,
            "name": "Sodacan Bob", "text": LINE_TEXT.format(n=n),
            "audio_path": None, "duration": 0.05, "created_at": time.time(),
        }
        wav = self.tmp / f"live_{n}.wav"
        shutil.copy(self.wav, wav)
        payload["audio_path"] = str(wav)
        ok = self.live_pane.perform_live_line(
            payload, self.slot, self.relay_dir, state_path=self.state_path,
            config=self.config, out=self.live_out)
        self.live_out.seek(0)
        self.live_out.truncate()
        return ok

    # -- one spoken line -----------------------------------------------------
    def speak_line(self, n):
        import gaze
        speaker = SLOTS[n % len(SLOTS)]
        if self.args.mode == "threaded":
            import gaze
            if n % 2:
                self._live_line(n, speaker)
            else:  # someone else speaks: heads turn, their mouth moves
                gaze.write_stage(os.path.join(self.relay_dir, "stage.json"),
                                 SLOTS[(n // 2) % 8 or 1], [self.slot], 1.0,
                                 started_at=time.time(),
                                 envelope=[0.2, 0.9, 0.4, 0.8, 0.1] * 4,
                                 envelope_rate_hz=20)
            time.sleep(self.args.line_wall_s)
            self.frames = self.ticks
            self.clock.t = 1_000_000.0 + self.ticks / SIM_FPS
            return
        if self.args.mode in ("live", "all"):
            self._live_line(n, speaker)
            # live-line stage.json lives in relay_dir; mirror it for the head
            self.stage_path = os.path.join(self.relay_dir, "stage.json")
            if self.avatar is not None:
                self.stage_gaze.stage_path = self.stage_path
        elif self.args.mode in ("text", "all"):
            from audio_envelope import compute_envelope
            wav = self.tmp / f"line_{n % 4}.wav"
            shutil.copy(self.wav, wav)
            envelope, rate = compute_envelope(wav)
            wav.unlink()
            gaze.write_stage(self.stage_path, speaker,
                             [SLOTS[(n + 1) % len(SLOTS)]], 6.0,
                             started_at=self.clock(), envelope=envelope or None,
                             envelope_rate_hz=rate, airing_id=f"air-{n // 50}")
            if speaker == self.slot:
                self.tile_pane.write_tile_state(
                    self.state_path, "speaking", bubble=LINE_TEXT.format(n=n))
            else:
                self.tile_pane.write_tile_state(self.state_path, "idle")
            self.renderer.refresh()
            self.sink.seek(0)
            self.sink.truncate()  # stands in for the tmux pty draining
        elif self.args.mode == "avatar":
            gaze.write_stage(self.stage_path, speaker, [SLOTS[(n + 1) % 8]], 6.0,
                             started_at=self.clock(),
                             envelope=[0.2, 0.9, 0.4, 0.8, 0.1] * 24,
                             envelope_rate_hz=20, airing_id=f"air-{n // 50}")
        if self.avatar is not None:
            for _ in range(self.args.frames_per_line):
                self.clock.advance(1.0 / SIM_FPS)
                (gaze_pose, mouth) = self.stage_gaze.sample()
                expression = "speaking" if speaker == self.slot else "idle"
                if not self.avatar.tick(expression, gaze=gaze_pose, mouth_open=mouth):
                    raise SystemExit("probe: avatar went inactive: render failed")
                self.frames += 1
        else:
            self.clock.advance(self.args.frames_per_line / SIM_FPS)

    # -- sampling ------------------------------------------------------------
    def sample(self, label=""):
        gc.collect()
        own, kids = sample_rss()
        sim_s = self.clock() - 1_000_000.0
        row = {"lines": self.lines_done, "frames": self.frames,
               "sim_min": round(sim_s / 60.0, 2),
               "wall_s": round(time.monotonic() - self.t0, 1),
               "rss_main_mb": round(own, 1), "rss_children_mb": round(kids, 1),
               "rss_total_mb": round(own + kids, 1)}
        self.samples.append(row)
        print(json.dumps(row), flush=True)
        return row

    def run(self):
        a = self.args
        if a.tracemalloc:
            tracemalloc.start(a.tracemalloc)
        # Warm-up: lazy imports, first GL buffers, glibc arenas. Excluded from
        # the slope so we measure growth, not start-up.
        for n in range(a.warmup_lines):
            self.speak_line(n)
        base = self.sample("warm")
        snap0 = tracemalloc.take_snapshot() if a.tracemalloc else None
        step = max(1, a.lines // a.samples)
        for n in range(a.warmup_lines, a.warmup_lines + a.lines):
            self.speak_line(n)
            self.lines_done += 1
            if self.lines_done % step == 0:
                self.sample()
        if not self.samples or self.samples[-1]["lines"] != self.lines_done:
            self.sample()
        result = self.summarize(base)
        if a.tracemalloc:
            snap1 = tracemalloc.take_snapshot()
            top = snap1.compare_to(snap0, "lineno")[:12]
            result["tracemalloc_top"] = [str(s) for s in top]
            print("tracemalloc top growth (python heap only):")
            for s in top:
                print("  ", s)
        return result

    def summarize(self, base):
        pts = [s for s in self.samples if s["frames"] >= base["frames"]
               and s is not base]
        pts = [base] + pts
        out = {"mode": self.args.mode, "slot": self.slot,
               "lines": self.lines_done, "frames": self.frames,
               "sim_hours": round(self.clock_hours(), 4)}
        for key in ("rss_main_mb", "rss_children_mb", "rss_total_mb"):
            out[key + "_start"] = pts[0][key]
            out[key + "_end"] = pts[-1][key]
            out[key + "_slope_mb_per_sim_hour"] = round(
                slope([p["sim_min"] / 60.0 for p in pts], [p[key] for p in pts]), 2)
            out[key + "_8d_growth_gb"] = round(
                out[key + "_slope_mb_per_sim_hour"] * 24 * 8 / 1024.0, 2)
        print(json.dumps(out, indent=2))
        return out

    def clock_hours(self):
        return (self.clock() - 1_000_000.0) / 3600.0

    def close(self):
        try:
            if getattr(self, "driver", None) is not None:
                self.driver.close()
            if self.renderer is not None:
                self.renderer.close()
            if self.avatar is not None:
                self.avatar.close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)


def slope(xs, ys):
    """Least-squares slope (y units per x unit)."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["avatar", "text", "live", "threaded", "all"], default="all")
    p.add_argument("--slot", default="tuber_3")
    p.add_argument("--lines", type=int, default=2000)
    p.add_argument("--frames-per-line", type=int, default=27,
                   help="head frames rendered per spoken line (27 frames x "
                        "2000 lines = 30 simulated minutes at 30 fps)")
    p.add_argument("--pace-speed", type=float, default=400.0,
                   help="Performer typing/sleep speed multiplier in live mode")
    p.add_argument("--driver-fps", type=float, default=120.0,
                   help="threaded mode: head tick rate (real air is 30; the "
                        "render is the bottleneck anyway)")
    p.add_argument("--line-wall-s", type=float, default=0.25,
                   help="threaded mode: wall seconds between lines")
    p.add_argument("--warmup-lines", type=int, default=20)
    p.add_argument("--samples", type=int, default=20)
    p.add_argument("--tracemalloc", type=int, default=0, metavar="NFRAMES",
                   help="enable tracemalloc with this stack depth (slow)")
    p.add_argument("--json", default=None, help="write the summary here")
    args = p.parse_args(argv)
    probe = Probe(args)
    try:
        result = probe.run()
    finally:
        probe.close()
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"summary": result, "samples": probe.samples}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
