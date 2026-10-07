#!/usr/bin/env python3
"""Repro for tile_pane RSS growth: pygame NOFRAME window + per-frame flip,
optionally X SHAPE re-masking, optionally pumping SDL events.

Runs INSIDE vtube-worker:latest with the repo's app/ on PYTHONPATH and an
Xvfb display. Prints RSS growth per mode. Usage (see run_tile_leak_repro.sh):
    python3 tile_leak_repro.py <mode> <seconds>
mode: flip | flip_pump | shape | shape_pump
"""
import math
import os
import sys
import time

import numpy as np


def rss_kb():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1])
    return 0


def frame(t, w=200, h=200):
    """A 'breathing' disc on black, so the silhouette changes every frame."""
    yy, xx = np.mgrid[0:h, 0:w]
    r = 60 + 8 * math.sin(t * 2.0)
    mouth = 6 + 5 * math.sin(t * 9.0)
    disc = (xx - w / 2) ** 2 + ((yy - h / 2) / 1.2) ** 2 < r * r
    jaw = (abs(xx - w / 2) < 25) & (yy > h / 2 + r * 0.9) & (yy < h / 2 + r * 0.9 + mouth)
    img = np.zeros((h, w, 3), np.float32)
    img[disc | jaw] = (0.2, 0.9, 0.3)
    return img


def main():
    mode, seconds = sys.argv[1], float(sys.argv[2])
    os.environ["SDL_VIDEO_WINDOW_POS"] = "100,100"
    import pygame
    pygame.display.init()
    screen = pygame.display.set_mode((200, 200), pygame.NOFRAME)
    shaper = None
    if mode.startswith("shape"):
        from window_shape import ShapeMasker, silhouette_mask
        shaper = ShapeMasker.create(pygame.display.get_wm_info().get("window"))
        assert shaper is not None, "SHAPE unavailable"
    pump = mode.endswith("_pump")

    start = time.monotonic()
    base = None
    frames = 0
    samples = []
    while time.monotonic() - start < seconds:
        t = time.monotonic() - start
        img = frame(t)
        if shaper is not None:
            shaper.apply(silhouette_mask(img))
        pixels = (np.clip(img, 0, 1) * 255).astype("uint8")
        surf = pygame.surfarray.make_surface(pixels.transpose(1, 0, 2))
        screen.blit(surf, (0, 0))
        pygame.display.flip()
        if pump:
            pygame.event.pump()
        frames += 1
        if frames == 60:  # warm-up done
            base = rss_kb()
        if base and frames % 240 == 0:
            samples.append(rss_kb() - base)
        time.sleep(1 / 12)
    growth = rss_kb() - (base or rss_kb())
    per_frame = growth * 1024 / max(1, frames - 60)
    print(f"{mode:11s} frames={frames} growth={growth} kB  per_frame={per_frame:.0f} B  "
          f"-> {per_frame * 12 * 86400 / 2**30:.2f} GiB/day  samples={samples}", flush=True)


if __name__ == "__main__":
    main()
