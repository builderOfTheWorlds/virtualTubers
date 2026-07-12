"""Tests for app/replay.py — display-only playback of session scripts.

The replayer's contract: render every performable event in order, never
execute recorded commands, and never crash the show over a bad event or an
unwritable avatar state file.
"""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import replay  # noqa: E402
from replay import (  # noqa: E402
    MAX_SCENE_SCALE, MIN_SCENE_SCALE, Pacer, Palette, Performer,
    estimate_event_seconds, load_script, _truncate_lines,
)
from agent_state import read_state  # noqa: E402


def make_performer(out, **kwargs):
    return Performer(
        out=out,
        pacer=Pacer(enabled=False),
        palette=Palette(enabled=False),
        **kwargs,
    )


SCRIPT = {
    "source": "2026-07-02_test",
    "events": [
        {"type": "user_message", "text": "Please add a heartbeat"},
        {"type": "assistant_text", "text": "On it, boss.\nStarting now."},
        {"type": "tool_call", "tool": "Read", "error": False,
         "input_summary": "app/agent.py", "output_summary": "", "detail_file": None,
         "detail": {"file": "app/agent.py"}},
        {"type": "tool_call", "tool": "Bash", "error": False,
         "input_summary": "git diff", "output_summary": "", "detail_file": None,
         "detail": {"command": "git diff --stat", "output": "1 file changed"}},
        {"type": "tool_call", "tool": "Edit", "error": True,
         "input_summary": "app/agent.py", "output_summary": "old_string not found",
         "detail_file": None,
         "detail": {"file": "app/agent.py", "old": "i = 0", "new": "i = 0  # tick"}},
        {"type": "tool_call", "tool": "ScheduleWakeup", "error": False,
         "input_summary": "delaySeconds=1500", "output_summary": "", "detail_file": None},
    ],
}


def test_perform_renders_all_events_in_order():
    out = io.StringIO()
    make_performer(out).perform(SCRIPT)
    text = out.getvalue()
    positions = [text.index(marker) for marker in (
        "REPLAY: 2026-07-02_test",
        "BOSS",
        "Please add a heartbeat",
        "KODI-7 ▸",
        "On it, boss.",
        "reading app/agent.py",
        "$ git diff --stat",
        "1 file changed",
        "editing app/agent.py",
        "- i = 0",
        "+ i = 0  # tick",
        "ScheduleWakeup: delaySeconds=1500",
        "fin",
    )]
    assert positions == sorted(positions)


def test_perform_marks_errored_tool_call():
    out = io.StringIO()
    make_performer(out).perform(SCRIPT)
    assert "✗ that didn't work" in out.getvalue()


def test_perform_respects_start_and_limit():
    out = io.StringIO()
    make_performer(out).perform(SCRIPT, start=1, limit=1)
    text = out.getvalue()
    assert "On it, boss." in text
    assert "BOSS" not in text
    assert "git diff" not in text


def test_perform_skips_unknown_event_types():
    out = io.StringIO()
    script = {"source": "x", "events": [{"type": "mystery"}, {"type": "assistant_text", "text": "hi"}]}
    make_performer(out).perform(script)
    assert "hi" in out.getvalue()


def test_avatar_state_written_and_show_survives_bad_path(tmp_path):
    # happy path: state file reflects the performance
    state_file = tmp_path / "agent_state.json"
    out = io.StringIO()
    make_performer(out, state_path=str(state_file)).perform(SCRIPT)
    state = read_state(str(state_file))
    assert state["expression"] == "happy"  # final "fin" state

    # unwritable path: show must still complete
    out2 = io.StringIO()
    bad = tmp_path / "nope" / "deeper" / "state.json"
    make_performer(out2, state_path=str(bad)).perform(SCRIPT)
    assert "fin" in out2.getvalue()


def test_long_output_is_truncated():
    out = io.StringIO()
    script = {"source": "x", "events": [
        {"type": "tool_call", "tool": "Bash", "error": False,
         "input_summary": "", "output_summary": "", "detail_file": None,
         "detail": {"command": "ls", "output": "\n".join(f"line{i}" for i in range(60))}},
    ]}
    make_performer(out, max_output_lines=10).perform(script)
    text = out.getvalue()
    assert "line9" in text
    assert "line10" not in text
    assert "(50 more lines)" in text


def test_truncate_lines_no_op_under_limit():
    lines, hidden = _truncate_lines("a\nb", 5)
    assert lines == ["a", "b"] and hidden == 0


def test_pacer_disabled_types_full_text_instantly():
    chunks = []
    Pacer(enabled=False).type_out(chunks.append, "hello world", cps=1)
    assert "".join(chunks) == "hello world"


# ── audio-anchored scenes (voiced shows from revoice.prepare_show) ──────────

class FakeNarrationAudio:
    def __init__(self, duration):
        self.audio_path = Path("scene.wav")
        self.duration = duration


class FakePlayback:
    def __init__(self):
        self.stopped = False
        self.waits = []

    @property
    def running(self):
        return not self.stopped

    def wait(self, timeout=None):
        self.waits.append(timeout)

    def stop(self):
        self.stopped = True


def test_estimate_event_seconds_grows_with_content():
    small = estimate_event_seconds({"type": "assistant_text", "text": "hi"})
    large = estimate_event_seconds({"type": "assistant_text", "text": "hi" * 500})
    assert 0 < small < large


def test_estimate_event_seconds_caps_output_at_max_lines():
    def shell_event(lines):
        return {"type": "tool_call", "tool": "Bash",
                "detail": {"command": "ls", "output": "\n".join("x" * lines for _ in range(lines))}}
    capped = estimate_event_seconds(shell_event(500), max_output_lines=10)
    assert capped < estimate_event_seconds(shell_event(500), max_output_lines=100)


def test_estimate_event_seconds_unknown_type_is_zero():
    assert estimate_event_seconds({"type": "mystery"}) == 0.0


def test_pacer_scale_layers_on_speed():
    pacer = Pacer(speed=2.0, enabled=False)
    assert pacer.effective_speed == 2.0
    pacer.scale = 0.5
    assert pacer.effective_speed == 1.0


def test_perform_voiced_show_syncs_to_audio(monkeypatch):
    played, scales = [], []
    playback = FakePlayback()

    def fake_play_wav(path, out=None):
        played.append(path)
        return playback

    waited = []
    monkeypatch.setattr(replay, "play_wav", fake_play_wav)
    monkeypatch.setattr(replay, "wait_extra",
                        lambda pb, started, min_seconds: waited.append(min_seconds))

    out = io.StringIO()
    performer = make_performer(out)
    original = performer._perform_events

    def spy_perform_events(events):
        scales.append(performer.pacer.scale)
        original(events)

    performer._perform_events = spy_perform_events

    show = [
        {"kind": "boss", "speaker": "boss", "events": [SCRIPT["events"][0]],
         "narration": "The boss wants a heartbeat.",
         "audio": FakeNarrationAudio(duration=4.0)},
        {"kind": "coder_talk", "speaker": "coder", "events": [SCRIPT["events"][1]],
         "narration": "Time to get to work.", "audio": None},  # silent scene
    ]
    performer.perform(SCRIPT, show=show)
    text = out.getvalue()

    # narration lines are shown for muted viewers, both scenes perform
    assert "♪ The boss wants a heartbeat." in text
    assert "♪ Time to get to work." in text
    assert "Please add a heartbeat" in text and "On it, boss." in text
    # only the voiced scene played audio, with a clamped sync scale applied
    assert len(played) == 1
    assert MIN_SCENE_SCALE <= scales[0] <= MAX_SCENE_SCALE
    assert scales[1] == 1.0  # silent scene runs at normal pacing
    assert performer.pacer.scale == 1.0  # reset after the show
    # disabled pacer (tests) stops audio instead of waiting on it
    assert playback.stopped and waited == []


def test_perform_voiced_show_waits_for_audio_when_paced(monkeypatch):
    playback = FakePlayback()
    waited = []
    monkeypatch.setattr(replay, "play_wav", lambda path, out=None: playback)
    monkeypatch.setattr(replay, "wait_extra",
                        lambda pb, started, min_seconds: waited.append(min_seconds))

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(speed=10_000.0),
                          palette=Palette(enabled=False))
    show = [{"kind": "coder_talk", "speaker": "coder",
             "events": [SCRIPT["events"][1]],
             "narration": "hi", "audio": FakeNarrationAudio(duration=0.5)}]
    performer.perform(SCRIPT, show=show)
    assert waited == [0.5]  # held the scene for the spoken line


def test_perform_voiced_show_respects_start_and_limit(monkeypatch):
    out = io.StringIO()
    show = [
        {"events": [SCRIPT["events"][0]], "narration": "one", "audio": None},
        {"events": [SCRIPT["events"][1]], "narration": "two", "audio": None},
        {"events": [SCRIPT["events"][3]], "narration": "three", "audio": None},
    ]
    make_performer(out).perform(SCRIPT, show=show, start=1, limit=1)
    text = out.getvalue()
    assert "♪ two" in text
    assert "♪ one" not in text and "♪ three" not in text


def test_load_script_from_json_and_directory(tmp_path):
    # JSON file
    p = tmp_path / "s.json"
    p.write_text(json.dumps(SCRIPT), encoding="utf-8")
    assert load_script(p)["source"] == "2026-07-02_test"

    # session directory (round-trips through the parser)
    d = tmp_path / "2026-07-01_00-00-00_ab12cd34"
    d.mkdir()
    (d / "conversation.md").write_text(
        "# Claude Session Log\n\n**Project:** virtualTubers\n"
        "**Session ID:** `ab12cd34`\n**Date:** 2026-07-01_00-00-00\n\n---\n\n"
        "## User\n\nhello\n",
        encoding="utf-8",
    )
    script = load_script(d)
    assert script["session_id"] == "ab12cd34"
    assert script["events"][0]["text"] == "hello"
