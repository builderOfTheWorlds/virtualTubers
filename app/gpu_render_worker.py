#!/usr/bin/env python3
"""
gpu_render_worker.py
Runs gl_raster's GPU rendering in a SEPARATE OS process from the one that
owns the avatar's pygame window — see codec_avatar.py's module docstring
and gl_raster.is_available()'s docstring for the full history, but in
short: on gx10 (aarch64, real NVIDIA GB10 GPU, 2026-09-22) a single
process cannot safely own both an in-process GL/EGL context AND an
SDL/pygame X11 window at the same time — sharing that Xvfb/driver
resource either crashes the window creation (X Error BadAccess) or
silently degrades the GL context to software rendering (llvmpipe),
depending on which is created first. There is no safe in-process
ordering.

Putting the GPU context in its own process, with no window at all,
sidesteps the conflict entirely: this worker process only ever touches
gl_raster/EGL, never pygame, so it gets real GPU acceleration whenever
the host has one. The avatar process only ever touches pygame, never
gl_raster directly, so its window is never at risk. They hand frames
across the process boundary via shared memory (multiprocessing.shared_
memory) rather than a pipe/Queue, since a Queue would pickle+copy the
whole frame (2304x864x3 bytes ≈ 5.6MB) through a pipe on every tick at
30fps (~170MB/s) — shared memory makes that a zero-copy write on one
side and a zero-copy read on the other.

GPURenderWorker duck-types FrameSource's public interface
(render_frame(expression) -> (img, backend)) so CodecAvatarProvider can
swap one for the other without changing render_tick() at all.
"""
import logging
import multiprocessing as mp
import os
import sys

import numpy as np

log = logging.getLogger(__name__)

#: Always use 'spawn', never the platform default. 'fork' (Linux's
#: default) would copy this process's memory into the child, including
#: any already-imported gl_raster/pygame modules and any already-created
#: GL context — exactly the shared-resource problem this module exists
#: to avoid. 'spawn' starts a genuinely fresh interpreter that only gets
#: what render_worker_main() below is explicitly given.
_CTX = mp.get_context("spawn")

#: How long render_frame() waits for the worker to answer one request
#: before deciding it's dead/stuck and raising, so callers can fall back
#: to in-process CPU rendering instead of hanging the avatar tick loop
#: forever (see codec_avatar.py's use of this).
DEFAULT_TIMEOUT_S = 5.0


def render_worker_main(cmd_q, result_q, shm_name, shape,
                       character_params, width, height, view_dist,
                       angle_speed, background=None):
    """Entry point for the child process (must be a module-level function
    — multiprocessing's 'spawn' context pickles a reference to it, which
    only works for something importable, not a closure or lambda).

    Owns exactly one thing: a FrameSource instance and the GL context it
    lazily creates via gl_raster. Never imports pygame, never creates a
    window — see the module docstring for why that separation is the
    whole point.
    """
    from multiprocessing import shared_memory

    # Defensive: AVATAR_HAS_PYGAME_WINDOW may already be set in this
    # process's OS environment if the parent (CodecAvatarProvider) set
    # it before spawning this worker — os.environ[...] = ... calls the
    # real OS setenv, so a child process inherits it regardless of fork
    # vs spawn. This worker never touches pygame, so that flag must NOT
    # apply here, or gl_raster.is_available() would wrongly refuse GPU
    # rendering in the one process that's actually safe to use it in.
    os.environ.pop("AVATAR_HAS_PYGAME_WINDOW", None)

    from avatar_providers.codec_avatar import FrameSource

    source = FrameSource(character_params, width=width, height=height,
                         view_dist=view_dist, angle_speed=angle_speed,
                         background=background)
    shm = shared_memory.SharedMemory(name=shm_name)
    out = np.ndarray(shape, dtype=np.uint8, buffer=shm.buf)

    try:
        while True:
            msg = cmd_q.get()
            if msg is None:  # shutdown sentinel
                break
            expression = msg
            try:
                img, backend = source.render_frame(expression)
                pixels = (np.clip(img, 0.0, 1.0) * 255).astype(np.uint8)
                out[:] = pixels
                result_q.put(("ok", backend))
            except Exception as exc:  # noqa: BLE001 — report, don't crash the worker
                log.exception("gpu_render_worker: render_frame failed")
                result_q.put(("error", repr(exc)))
    finally:
        shm.close()


class GPURenderWorker:
    """Drop-in replacement for FrameSource that renders in a dedicated
    subprocess instead of in-process — see the module docstring for why.

    Duck-types FrameSource's public surface used by codec_avatar.py:
    .faces (read once, for the startup log line) and
    .render_frame(expression) -> (img: (H,W,3) uint8 0..255, backend: str).
    Note the dtype difference from FrameSource (float 0..1) — callers
    that need float should not rely on this being interchangeable at
    the byte level, only at the call-signature level; codec_avatar.py's
    render_tick() already converts FrameSource's float output to uint8
    itself, so this just does that conversion worker-side instead.

    `background` is forwarded verbatim to the child's FrameSource, so the
    grey-console compositing happens worker-side and the frame that lands
    in shared memory is already final. It must be a concrete value
    (a (3,) 0..1 array or None) — NOT codec_avatar._UNSET, whose object()
    identity does not survive the pickling 'spawn' does. Defaults to None
    (no compositing) so a direct constructor call keeps the original
    black-surround behavior.
    """

    def __init__(self, character_params, width, height, view_dist,
                angle_speed, timeout_s=DEFAULT_TIMEOUT_S, background=None):
        from multiprocessing import shared_memory
        self.width = width
        self.height = height
        self.timeout_s = timeout_s
        self._shape = (height, width, 3)
        nbytes = int(np.prod(self._shape))

        self._shm = shared_memory.SharedMemory(create=True, size=nbytes)
        self._frame = np.ndarray(self._shape, dtype=np.uint8, buffer=self._shm.buf)
        self._cmd_q = _CTX.Queue()
        self._result_q = _CTX.Queue()

        # faces is read by CodecAvatarProvider's startup log line
        # (`len(self._source.faces)`) before any frame is rendered —
        # build the mesh here too (cheap, pure numpy, no GL) so that
        # still works without needing a round-trip to the worker.
        from codec_head import build_codec_head
        _verts, self.faces, _materials = build_codec_head(character_params)

        self._proc = _CTX.Process(
            target=render_worker_main,
            args=(self._cmd_q, self._result_q, self._shm.name, self._shape,
                  character_params, width, height, view_dist, angle_speed,
                  background),
            daemon=True,
        )
        self._proc.start()
        self._closed = False

    def render_frame(self, expression):
        """Ask the worker to render one frame, block for the result, and
        return a frame with the SAME contract as FrameSource.render_frame:
        an (H,W,3) float array in 0..1 — NOT the raw uint8 0..255 bytes
        the shared-memory buffer actually holds. That conversion happens
        here, not in the caller, specifically so this class is a true
        drop-in for FrameSource — codec_avatar.py's render_tick() does
        its own `np.clip(img, 0, 1) * 255` unconditionally, and would
        silently corrupt the image (clip nearly everything to 0) if
        handed uint8 0..255 values instead. Raises RuntimeError on a
        worker crash/timeout so callers can fall back rather than
        silently reusing a stale or garbage frame forever."""
        if self._closed:
            raise RuntimeError("GPURenderWorker: render_frame() called after close()")
        if not self._proc.is_alive():
            raise RuntimeError(
                "GPURenderWorker: render worker process is no longer alive")

        self._cmd_q.put(expression)
        try:
            status, payload = self._result_q.get(timeout=self.timeout_s)
        except Exception as exc:  # noqa: BLE001 — queue.Empty or similar
            raise RuntimeError(
                f"GPURenderWorker: no response from render worker within "
                f"{self.timeout_s}s"
            ) from exc
        if status == "error":
            raise RuntimeError(f"GPURenderWorker: render failed in worker: {payload}")
        backend = payload
        return self._frame.astype(np.float32) / 255.0, backend

    def close(self):
        """Best-effort, idempotent shutdown — never raises, since this is
        typically called from cleanup paths that shouldn't themselves
        need error handling."""
        if self._closed:
            return
        self._closed = True
        try:
            self._cmd_q.put(None)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.join(timeout=2.0)
            if self._proc.is_alive():
                self._proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._shm.close()
            self._shm.unlink()
        except Exception:  # noqa: BLE001
            pass

    def __del__(self):
        self.close()
