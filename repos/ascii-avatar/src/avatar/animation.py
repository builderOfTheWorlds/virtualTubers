"""Real-time animation compositor for the avatar.

Layers micro-events (blinks, glitch bursts, holographic flicker) on top
of the base state animation.  The compositor owns the frame selection
logic and injects overlay frames at randomized intervals to make the
avatar feel alive even when idle.

The pre-computed frame dict from converter.generate_state_frames() must
include these special keys:
  - "blink":   4 frames (half-close, close, close, half-open)
  - "glitch":  6 frames (escalating chromatic aberration + displacement)
  - "flicker": 3 frames (brightness drop variants)
"""

from __future__ import annotations

import random
import time
import threading
from dataclasses import dataclass, field
from enum import Enum, auto


class MicroEvent(Enum):
    """Overlay events that fire independently of the state machine."""
    NONE = auto()
    BLINK = auto()
    GLITCH_BURST = auto()
    FLICKER = auto()


@dataclass
class MicroEventScheduler:
    """Schedules random micro-events on independent timers.

    Each event type has a (min, max) interval range in seconds.
    When an event fires, it plays through its frame sequence before
    allowing the next event.
    """
    blink_interval: tuple[float, float] = (3.0, 7.0)
    glitch_interval: tuple[float, float] = (10.0, 25.0)
    flicker_interval: tuple[float, float] = (6.0, 18.0)

    # Internal state
    _active_event: MicroEvent = field(default=MicroEvent.NONE, init=False)
    _event_frame_idx: int = field(default=0, init=False)
    _event_frame_count: int = field(default=0, init=False)
    _next_blink: float = field(default=0.0, init=False)
    _next_glitch: float = field(default=0.0, init=False)
    _next_flicker: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        now = time.monotonic()
        # Stagger initial events so they don't all fire at once
        self._next_blink = now + random.uniform(1.0, self.blink_interval[1])
        self._next_glitch = now + random.uniform(5.0, self.glitch_interval[1])
        self._next_flicker = now + random.uniform(3.0, self.flicker_interval[1])

    def tick(self, frame_counts: dict[str, int]) -> tuple[MicroEvent, int]:
        """Called each render cycle.  Returns (event_type, frame_index).

        If no micro-event is active, returns (NONE, 0).
        If an event is playing, returns the event type and current frame
        index within its sequence.

        Args:
            frame_counts: {"blink": N, "glitch": N, "flicker": N} —
                number of frames available for each overlay type.
        """
        with self._lock:
            now = time.monotonic()

            # If an event is currently playing, advance it
            if self._active_event != MicroEvent.NONE:
                self._event_frame_idx += 1
                if self._event_frame_idx >= self._event_frame_count:
                    # Event finished
                    finished = self._active_event
                    self._active_event = MicroEvent.NONE
                    self._event_frame_idx = 0
                    # Reschedule
                    if finished == MicroEvent.BLINK:
                        # Occasional double-blink
                        if random.random() < 0.25:
                            self._next_blink = now + 0.3
                        else:
                            self._next_blink = now + random.uniform(*self.blink_interval)
                    elif finished == MicroEvent.GLITCH_BURST:
                        self._next_glitch = now + random.uniform(*self.glitch_interval)
                    elif finished == MicroEvent.FLICKER:
                        self._next_flicker = now + random.uniform(*self.flicker_interval)
                    return (MicroEvent.NONE, 0)
                return (self._active_event, self._event_frame_idx)

            # Check if any event should fire (priority: blink > glitch > flicker)
            if now >= self._next_blink and "blink" in frame_counts:
                self._active_event = MicroEvent.BLINK
                self._event_frame_idx = 0
                self._event_frame_count = frame_counts["blink"]
                return (MicroEvent.BLINK, 0)

            if now >= self._next_glitch and "glitch" in frame_counts:
                self._active_event = MicroEvent.GLITCH_BURST
                self._event_frame_idx = 0
                self._event_frame_count = frame_counts["glitch"]
                return (MicroEvent.GLITCH_BURST, 0)

            if now >= self._next_flicker and "flicker" in frame_counts:
                self._active_event = MicroEvent.FLICKER
                self._event_frame_idx = 0
                self._event_frame_count = frame_counts["flicker"]
                return (MicroEvent.FLICKER, 0)

            return (MicroEvent.NONE, 0)

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active_event != MicroEvent.NONE


# Map MicroEvent types to frame dict keys
_EVENT_FRAME_KEYS = {
    MicroEvent.BLINK: "blink",
    MicroEvent.GLITCH_BURST: "glitch",
    MicroEvent.FLICKER: "flicker",
}


class AnimationCompositor:
    """Composites base state frames with micro-event overlays.

    Replaces the simple frame_index cycling in the main loop with a
    richer animation system that layers:
    1. Base state animation (idle/thinking/speaking/listening/error)
    2. Micro-event overlays (blink/glitch/flicker) during idle/listening

    Usage:
        compositor = AnimationCompositor(frames_dict)
        # In render loop:
        frame = compositor.get_frame(state, frame_index, mouth_override)
        rate = compositor.get_frame_rate(state)
    """

    # States where micro-events can fire
    MICRO_EVENT_STATES = {"idle", "listening"}

    def __init__(
        self,
        frames: dict[str, list[str]],
        rates: dict[str, float],
    ) -> None:
        self._frames = frames
        self._rates = rates
        self._scheduler = MicroEventScheduler()

        # Build frame count map for overlay types
        self._overlay_counts = {}
        for event_type, key in _EVENT_FRAME_KEYS.items():
            if key in frames:
                self._overlay_counts[key] = len(frames[key])

    def get_frame(
        self,
        state_value: str,
        frame_index: int,
        mouth_frame_override: int | None = None,
    ) -> str:
        """Get the composited frame for the current render cycle.

        During idle/listening states, micro-events may override the
        base frame with overlay frames (blink, glitch, flicker).
        """
        # Check for micro-event overlay
        if state_value in self.MICRO_EVENT_STATES:
            event_type, event_frame = self._scheduler.tick(self._overlay_counts)
            if event_type != MicroEvent.NONE:
                key = _EVENT_FRAME_KEYS[event_type]
                overlay_frames = self._frames.get(key, [])
                if overlay_frames and event_frame < len(overlay_frames):
                    return overlay_frames[event_frame]

        # Base state frame
        frames = self._frames.get(state_value, self._frames.get("idle", [""]))
        if not frames:
            return ""

        if mouth_frame_override is not None and state_value == "speaking":
            idx = mouth_frame_override % len(frames)
        else:
            idx = frame_index % len(frames)
        return frames[idx]

    def get_frame_rate(self, state_value: str) -> float:
        """Get the frame rate, potentially adjusted during micro-events."""
        base_rate = self._rates.get(state_value, 0.8)
        if state_value in self.MICRO_EVENT_STATES and self._scheduler.is_active:
            # Micro-events render faster for snappier animation
            return min(base_rate, 0.12)
        return base_rate

    @property
    def has_overlays(self) -> bool:
        """Whether overlay frames are available."""
        return bool(self._overlay_counts)
