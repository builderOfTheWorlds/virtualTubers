#!/usr/bin/env python3
"""Repro 2: the REAL tile head — tile_avatar.TileAvatar (CodecAvatarProvider +
GPURenderWorker subprocess + SHAPE) driven at 12 fps with a moving gaze and
mouth, exactly as TileAvatarDriver does. Measures RSS of THIS process (the
tile) and of its render-worker child separately.

    python3 tile_leak_repro2.py <seconds> [cpu]
`cpu` forces the in-process FrameSource (gpu_subprocess off) for comparison.
"""
import math
import os
import sys
import time

import yaml


def rss_kb(pid="self"):
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS"):
                return int(line.split()[1])
    except OSError:
        pass
    return 0


def children():
    me = str(os.getpid())
    out = []
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                if open(f"/proc/{p}/stat").read().split()[3] == me:
                    out.append(p)
            except OSError:
                pass
    return out


def main():
    seconds = float(sys.argv[1])
    force_cpu = len(sys.argv) > 2 and sys.argv[2] == "cpu"
    import tile_avatar
    config = yaml.safe_load(open("/repo_config/workers/roundtable.yaml"))
    params = tile_avatar.resolve_slot_character_params(config, "tuber_1")
    assert params, "no character params for tuber_1"
    if force_cpu:
        orig = tile_avatar.build_tile_avatar_config

        def no_sub(*a, **k):
            cfg = orig(*a, **k)
            cfg["codec_avatar"]["gpu_subprocess"] = False
            return cfg
        tile_avatar.build_tile_avatar_config = no_sub
    av = tile_avatar.TileAvatar("tuber_1", params, (100, 100, 200, 200))
    assert av.active, "head did not start"

    start = time.monotonic()
    frames, base, base_kids, samples = 0, None, None, []
    while time.monotonic() - start < seconds:
        t = time.monotonic() - start
        gaze = (0.4 * math.sin(t * 0.3), 0.05 * math.sin(t * 0.7))
        mouth = max(0.0, math.sin(t * 9.0))
        expr = "speaking" if int(t) % 6 < 3 else "listening"
        assert av.tick(expr, gaze=gaze, mouth_open=mouth), "tick failed"
        frames += 1
        if frames == 120:
            base = rss_kb()
            base_kids = sum(rss_kb(c) for c in children())
        if base and frames % 600 == 0:
            samples.append((rss_kb() - base, sum(rss_kb(c) for c in children()) - base_kids))
        time.sleep(1 / 12)
    growth = rss_kb() - base
    kid_growth = sum(rss_kb(c) for c in children()) - base_kids
    pf = growth * 1024 / max(1, frames - 120)
    print(f"{'cpu' if force_cpu else 'gpu'} frames={frames} backend={getattr(av._provider._source, 'last_backend', '?')} "
          f"tile_growth={growth} kB ({pf:.0f} B/frame -> {pf * 12 * 86400 / 2**30:.2f} GiB/day) "
          f"worker_growth={kid_growth} kB samples(tile,worker)={samples}", flush=True)
    av.close()


if __name__ == "__main__":
    main()
