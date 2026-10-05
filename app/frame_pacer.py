#!/usr/bin/env python3
"""
frame_pacer.py
Deadline-based frame pacing for the live avatar render loops.

WHY THIS EXISTS. Both avatar loops (app/avatar.py's main loop and
tile_pane.TileAvatarDriver) used to do `render(); sleep(1/fps)`. That sleeps
a FULL frame interval on top of however long the render took, so the real
rate is 1/(interval + render_time), never the configured fps. Measured live
on 2026-10-05: tiles configured for 12fps redrew at ~6-10fps, and the GM's
solo 30fps head redrew at ~9fps. On a 30fps capture that is the "choppy"
look: uneven 2-5 frame holds between head updates.

FramePacer instead keeps an absolute schedule (t0, t0+T, t0+2T, ...) and
waits only for the time REMAINING until the next slot. A frame that overruns
its slot is not "made up" with a burst of back-to-back renders afterwards:
the schedule re-anchors to now, so a hiccup costs one late frame rather than
a stutter-then-sprint.

Pure stdlib, no display — unit-testable with an injected clock and sleeper.
"""
import time


class FramePacer:
    """Paces a render loop to `fps` frames per second.

    Usage::

        pacer = FramePacer(30)
        while running:
            render()
            pacer.wait()          # or pacer.wait(stop_event.wait)

    `sleep` is any callable taking seconds — time.sleep by default, or a
    threading.Event's .wait so a stop request still interrupts the wait.
    Its return value is passed back from wait() (Event.wait returns True
    when the event was set), so callers can stop on it.
    """

    def __init__(self, fps, clock=time.monotonic, sleep=time.sleep):
        fps = float(fps)
        if not fps > 0:
            raise ValueError(f"FramePacer: fps must be > 0, got {fps!r}")
        self.interval_s = 1.0 / fps
        self._clock = clock
        self._sleep = sleep
        self._next = None

    def wait(self, sleep=None):
        """Block until the next frame slot. Returns whatever the sleeper
        returned (None for time.sleep), or None when no wait was needed."""
        sleeper = sleep or self._sleep
        now = self._clock()
        if self._next is None:
            self._next = now
        self._next += self.interval_s
        delay = self._next - now
        if delay <= 0:
            # Overran the slot: re-anchor instead of bursting to catch up.
            self._next = now
            return sleeper(0) if sleep is not None else None
        return sleeper(delay)


__all__ = ["FramePacer"]
