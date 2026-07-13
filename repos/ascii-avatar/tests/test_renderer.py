import time
import pytest

from avatar.renderer import AvatarRenderer
from avatar.state_machine import AvatarState


class FakeTerminal:
    """Fake blessed terminal for headless testing."""

    def __init__(self, width=80, height=24, colors=256):
        self.width = width
        self.height = height
        self.number_of_colors = colors
        self.output = []
        self._location_ctx = self

    def clear(self):
        self.output.append("CLEAR")
        return ""

    def move_xy(self, x, y):
        return f"MOVE({x},{y})"

    def home(self):
        return "HOME"

    def normal(self):
        return "NORMAL"

    def hidden_cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def location(self, x=0, y=0):
        return self

    def inkey(self, timeout=0):
        class Key:
            def __init__(self):
                self.name = None
            def __eq__(self, other):
                return False
        return Key()


class TestRenderer:
    def test_create_renderer(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk")
        assert r is not None

    def test_current_frame_changes_with_state(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk")
        frame_idle = r.get_current_frame(AvatarState.IDLE, frame_index=0)
        frame_think = r.get_current_frame(AvatarState.THINKING, frame_index=0)
        # Different states should produce different frames
        assert frame_idle != frame_think

    def test_frame_index_cycles(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk")
        idx = r.next_frame_index(AvatarState.IDLE, current_index=0)
        # Should cycle within the idle frame count
        assert isinstance(idx, int)
        assert idx >= 0

    def test_status_bar_content(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk")

        # Never received: waiting
        bar_waiting = r.format_status_bar(
            state=AvatarState.IDLE,
            connected=False,
            tts_loaded=False,
            last_event="",
            time_since_last_event=None,
        )
        assert "IDLE" in bar_waiting
        assert "waiting" in bar_waiting

        # Recent event: connected
        bar_connected = r.format_status_bar(
            state=AvatarState.IDLE,
            connected=True,
            tts_loaded=False,
            last_event="state_change",
            time_since_last_event=1.0,
        )
        assert "IDLE" in bar_connected
        assert "● connected" in bar_connected
        assert "stale" not in bar_connected

        # Stale event: connected (stale)
        bar_stale = r.format_status_bar(
            state=AvatarState.IDLE,
            connected=True,
            tts_loaded=False,
            last_event="state_change",
            time_since_last_event=90.0,
        )
        assert "stale" in bar_stale

    def test_monochrome_fallback(self):
        term = FakeTerminal(colors=2)
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk")
        frame = r.get_current_frame(AvatarState.IDLE, frame_index=0)
        # ANSI color codes should be stripped
        assert "\033[36m" not in frame

    def test_frame_rate_from_state(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk", frame_rate_modifier=1.0)
        rate = r.get_frame_rate(AvatarState.IDLE)
        assert rate == pytest.approx(0.8, abs=0.01)

    def test_frame_rate_modifier(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk", frame_rate_modifier=0.5)
        rate = r.get_frame_rate(AvatarState.IDLE)
        assert rate == pytest.approx(0.4, abs=0.01)
