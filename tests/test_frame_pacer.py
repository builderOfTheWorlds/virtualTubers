"""Tests for app/frame_pacer.py — deadline-based avatar frame pacing."""
import pytest

from frame_pacer import FramePacer


class FakeClock:
    def __init__(self):
        self.t = 100.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 6))
        self.t += s


def test_wait_subtracts_render_time_from_the_interval():
    clock = FakeClock()
    pacer = FramePacer(10, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        clock.t += 0.03  # a 30ms render
        pacer.wait()
    # 100ms frames: each wait covers only what the render didn't use.
    assert clock.sleeps[1:] == [0.07, 0.07]


def test_achieved_rate_matches_target_despite_render_cost():
    clock = FakeClock()
    pacer = FramePacer(30, clock=clock, sleep=clock.sleep)
    start = clock.t
    for _ in range(300):
        clock.t += 0.02  # 20ms of a 33ms budget
        pacer.wait()
    assert clock.t - start == pytest.approx(10.0, abs=0.05)


def test_overrun_reanchors_instead_of_bursting():
    clock = FakeClock()
    pacer = FramePacer(10, clock=clock, sleep=clock.sleep)
    pacer.wait()
    clock.t += 0.5  # a big stall
    pacer.wait()    # late: no sleep
    clock.t += 0.01
    pacer.wait()
    # Next frame gets a full slot from the re-anchor, not a 0s catch-up.
    assert clock.sleeps[-1] == pytest.approx(0.09)


def test_wait_returns_the_sleepers_result_so_stop_events_work():
    clock = FakeClock()
    pacer = FramePacer(10, clock=clock, sleep=lambda s: True)
    assert pacer.wait() is True


@pytest.mark.parametrize("fps", [0, -5])
def test_non_positive_fps_is_rejected(fps):
    with pytest.raises(ValueError):
        FramePacer(fps)
