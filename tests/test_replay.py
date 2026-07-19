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


def test_speaker_names_override_display_name_for_tagged_scene():
    """A scene tagged with a "speaker" that maps through speaker_names
    prints that persona's name instead of the performing worker's own —
    the on-screen half of multi-speaker duet narration (docs/revoice.md)."""
    out = io.StringIO()
    performer = make_performer(out, speaker_names={"tester": "TESS-3"})
    show = [
        {"kind": "coder_talk", "speaker": "tester",
         "events": [SCRIPT["events"][1]], "narration": None, "audio": None},
    ]
    performer.perform(SCRIPT, show=show)
    text = out.getvalue()
    assert "TESS-3 ▸" in text
    assert "KODI-7 ▸" not in text
    assert performer._display_name == performer.worker_name  # reset after


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


# ── duet scenes: owned / target_duration (docs/duet_replay.md) ─────────────

def test_solo_show_unchanged_with_no_hooks_and_no_owned_key():
    """A scene dict with neither "owned" nor "target_duration" (today's
    revoice.py output) must behave identically to before — this is the
    regression guard for the duet changes to _perform_scene/perform."""
    out = io.StringIO()
    performer = make_performer(out)
    show = [
        {"kind": "boss", "speaker": "boss", "events": [SCRIPT["events"][0]],
         "narration": "The boss wants a heartbeat.", "audio": None},
    ]
    performer.perform(SCRIPT, show=show)
    text = out.getvalue()
    assert "♪ The boss wants a heartbeat." in text
    assert performer.on_scene_start is None
    assert performer.wait_for_scene is None


def test_unowned_scene_with_target_duration_scales_and_holds_no_audio(monkeypatch):
    played = []
    monkeypatch.setattr(replay, "play_wav", lambda path, out=None: played.append(path))
    slept = []
    monkeypatch.setattr(replay.time, "sleep", lambda s: slept.append(s))

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(enabled=True), palette=Palette(enabled=False))
    show = [
        {"kind": "coder_talk", "speaker": "coder", "events": [SCRIPT["events"][1]],
         "narration": "Coder's take.", "audio": None,
         "owned": False, "target_duration": 5.0},
    ]
    performer.perform(SCRIPT, show=show)

    assert played == []  # never plays audio for a scene it doesn't own
    # held roughly for target_duration (mocked sleep records the remainder)
    assert slept and max(slept) == pytest.approx(5.0, abs=0.2)


def test_owned_scene_without_audio_but_target_duration_also_holds(monkeypatch):
    """Spec: owned scenes fall into the target_duration path too when audio
    is None (e.g. a duet reuse that dropped this worker's own WAV)."""
    played = []
    monkeypatch.setattr(replay, "play_wav", lambda path, out=None: played.append(path))
    slept = []
    monkeypatch.setattr(replay.time, "sleep", lambda s: slept.append(s))

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(enabled=True), palette=Palette(enabled=False))
    show = [
        {"kind": "coder_talk", "speaker": "coder", "events": [SCRIPT["events"][1]],
         "narration": "Coder's take.", "audio": None,
         "owned": True, "target_duration": 3.0},
    ]
    performer.perform(SCRIPT, show=show)

    assert played == []
    assert slept and max(slept) == pytest.approx(3.0, abs=0.2)


def test_target_duration_hold_skipped_when_pacing_disabled():
    """--no-delay / fast-forward mode must not block on the hold."""
    out = io.StringIO()
    performer = make_performer(out)  # Pacer(enabled=False)
    show = [
        {"kind": "coder_talk", "speaker": "coder", "events": [SCRIPT["events"][1]],
         "narration": "fast", "audio": None,
         "owned": False, "target_duration": 50.0},
    ]
    import time as real_time
    started = real_time.monotonic()
    performer.perform(SCRIPT, show=show)
    assert real_time.monotonic() - started < 2.0  # did not actually wait 50s


def test_owned_scene_with_audio_plays_even_when_target_duration_present(monkeypatch):
    """Case A (owned + real audio) still wins over target_duration when both
    are present — today's audio-anchored path is untouched."""
    playback = FakePlayback()
    monkeypatch.setattr(replay, "play_wav", lambda path, out=None: playback)
    waited = []
    monkeypatch.setattr(replay, "wait_extra",
                        lambda pb, started, min_seconds: waited.append(min_seconds))

    out = io.StringIO()
    performer = make_performer(out)
    show = [
        {"kind": "boss", "speaker": "boss", "events": [SCRIPT["events"][0]],
         "narration": "hi", "audio": FakeNarrationAudio(duration=2.0),
         "owned": True, "target_duration": 999.0},
    ]
    performer.perform(SCRIPT, show=show)
    assert playback.stopped  # disabled pacer stops rather than waits
    assert waited == []


def test_unowned_scene_sets_idle_avatar_not_speaking(tmp_path):
    state_file = tmp_path / "agent_state.json"
    calls = []
    out = io.StringIO()
    performer = make_performer(out, state_path=str(state_file))
    original_avatar = performer._avatar

    def spy(expression, action="", bubble=None):
        calls.append((expression, action, bubble))
        original_avatar(expression, action=action, bubble=bubble)

    performer._avatar = spy
    show = [
        {"kind": "boss", "speaker": "boss", "events": [SCRIPT["events"][0]],
         "narration": "the boss speaks", "audio": None,
         "owned": False, "target_duration": 0.01},
    ]
    performer.perform(SCRIPT, show=show)
    scene_calls = [c for c in calls if c[0] == "idle" and c[1] == "listening to the show"]
    assert scene_calls == [("idle", "listening to the show", None)]
    speaking_calls = [c for c in calls if c[0] == "speaking" and c[2] == "the boss speaks"]
    assert speaking_calls == []  # never shows the bubble for an unowned scene


def test_owned_scene_sets_speaking_avatar_with_bubble():
    calls = []
    out = io.StringIO()
    performer = make_performer(out)
    original_avatar = performer._avatar

    def spy(expression, action="", bubble=None):
        calls.append((expression, action, bubble))
        original_avatar(expression, action=action, bubble=bubble)

    performer._avatar = spy
    show = [
        {"kind": "boss", "speaker": "boss", "events": [SCRIPT["events"][0]],
         "narration": "the boss speaks", "audio": None, "owned": True},
    ]
    performer.perform(SCRIPT, show=show)
    assert ("speaking", "narrating the rerun", "the boss speaks") in calls


# ── duet hooks: on_scene_start / wait_for_scene (docs/duet_replay.md) ──────

def test_on_scene_start_called_once_per_scene_in_order_before_wait_for_scene():
    order = []
    show = [
        {"events": [SCRIPT["events"][0]], "narration": "s0", "audio": None},
        {"events": [SCRIPT["events"][1]], "narration": "s1", "audio": None},
    ]

    def on_scene_start(i):
        order.append(("start", i))

    def wait_for_scene(i):
        order.append(("wait", i))
        return i

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(enabled=False), palette=Palette(enabled=False),
                          on_scene_start=on_scene_start, wait_for_scene=wait_for_scene)
    performer.perform(SCRIPT, show=show)
    assert order == [("start", 0), ("wait", 0), ("start", 1), ("wait", 1)]


def test_on_scene_start_exception_is_swallowed_and_show_continues():
    def blowing_up(i):
        raise RuntimeError("cue publish failed")

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(enabled=False), palette=Palette(enabled=False),
                          on_scene_start=blowing_up)
    show = [
        {"events": [SCRIPT["events"][0]], "narration": "s0", "audio": None},
        {"events": [SCRIPT["events"][1]], "narration": "s1", "audio": None},
    ]
    performer.perform(SCRIPT, show=show)  # must not raise
    text = out.getvalue()
    assert "♪ s0" in text and "♪ s1" in text and "fin" in text


def test_wait_for_scene_proceed_matches_solo_output():
    """wait_for_scene(i) -> i every time (no jump) must render identically
    to the no-hook case."""
    show = [
        {"events": [SCRIPT["events"][0]], "narration": "s0", "audio": None},
        {"events": [SCRIPT["events"][1]], "narration": "s1", "audio": None},
    ]

    out_plain = io.StringIO()
    make_performer(out_plain).perform(SCRIPT, show=[dict(s) for s in show])

    out_hooked = io.StringIO()
    performer = Performer(out=out_hooked, pacer=Pacer(enabled=False),
                          palette=Palette(enabled=False),
                          wait_for_scene=lambda i: i)
    performer.perform(SCRIPT, show=[dict(s) for s in show])

    assert out_plain.getvalue() == out_hooked.getvalue()


def test_wait_for_scene_fast_forwards_when_two_or_more_behind():
    show = [
        {"events": [SCRIPT["events"][0]], "narration": "s0", "audio": None},
        {"events": [SCRIPT["events"][1]], "narration": "s1", "audio": None},
        {"events": [SCRIPT["events"][3]], "narration": "s2", "audio": None},
        {"events": [SCRIPT["events"][1]], "narration": "s3", "audio": None},
    ]
    wait_calls = []

    def wait_for_scene(i):
        wait_calls.append(i)
        return 3 if i == 0 else i  # first call authorizes jumping to scene 3

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(enabled=True), palette=Palette(enabled=False),
                          wait_for_scene=wait_for_scene)
    enabled_during = []
    original_perform_scene = performer._perform_scene

    def spy(scene):
        enabled_during.append(performer.pacer.enabled)
        original_perform_scene(scene)

    performer._perform_scene = spy
    performer.perform(SCRIPT, show=show)

    assert wait_calls == [0]  # one wait covers the whole catch-up batch
    assert enabled_during == [True, False, False, False]
    text = out.getvalue()
    for marker in ("♪ s0", "♪ s1", "♪ s2", "♪ s3", "fin"):
        assert marker in text
    assert performer.pacer.enabled is True  # restored after catch-up


def test_wait_for_scene_abort_ends_show_early_and_sets_idle():
    calls = []
    out = io.StringIO()
    performer = make_performer(out, wait_for_scene=lambda i: -1)
    original_avatar = performer._avatar

    def spy(expression, action="", bubble=None):
        calls.append((expression, action, bubble))
        original_avatar(expression, action=action, bubble=bubble)

    performer._avatar = spy
    show = [
        {"events": [SCRIPT["events"][0]], "narration": "s0", "audio": None},
        {"events": [SCRIPT["events"][1]], "narration": "s1", "audio": None},
    ]
    ok = performer.perform(SCRIPT, show=show)
    text = out.getvalue()
    assert ok is False
    assert "interrupted" in text
    assert "fin" not in text
    assert "♪ s0" not in text  # aborted before performing any scene
    assert calls[-1][0] == "idle"


def test_perform_returns_true_on_natural_completion():
    out = io.StringIO()
    assert make_performer(out).perform(SCRIPT) is True


# ── operator replay_stop (docs/operator_commands.md, docs/replay_pane.md) ──

def test_pacer_check_stop_raises_when_should_stop_true():
    pacer = Pacer(should_stop=lambda: True)
    with pytest.raises(replay.ReplayStopped):
        pacer.check_stop()


def test_pacer_sleep_and_type_out_unaffected_when_should_stop_false():
    pacer = Pacer(enabled=False, should_stop=lambda: False)
    pacer.sleep(1)  # must not raise
    chunks = []
    pacer.type_out(chunks.append, "hi", cps=1)
    assert "".join(chunks) == "hi"


def test_perform_stops_mid_show_and_sets_idle():
    """An operator replay_stop (should_stop firing partway through) unwinds
    cleanly instead of raising out of perform() — same shutdown shape as
    the existing wait_for_scene abort, but reachable from a solo show with
    no duet hooks at all."""
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 3

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(speed=1000.0, should_stop=should_stop),
                          palette=Palette(enabled=False))
    avatar_calls = []
    original_avatar = performer._avatar

    def spy(expression, action="", bubble=None):
        avatar_calls.append((expression, action, bubble))
        original_avatar(expression, action=action, bubble=bubble)

    performer._avatar = spy
    ok = performer.perform(SCRIPT)
    text = out.getvalue()
    assert ok is False
    assert "stopped" in text
    assert "fin" not in text
    assert avatar_calls[-1][0] == "idle"


def test_replay_stopped_mid_scene_stops_in_flight_audio(monkeypatch):
    """A stop firing while a voiced scene's audio is playing must stop that
    audio too — otherwise narration keeps playing under a show that already
    ended (_perform_scene's ReplayStopped handler)."""
    playback = FakePlayback()
    monkeypatch.setattr(replay, "play_wav", lambda path, out=None: playback)

    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(speed=1000.0, should_stop=should_stop),
                          palette=Palette(enabled=False))
    show = [
        {"kind": "boss", "speaker": "boss", "events": [SCRIPT["events"][0]],
         "narration": "hi", "audio": FakeNarrationAudio(duration=5.0)},
    ]
    ok = performer.perform(SCRIPT, show=show)
    assert ok is False
    assert playback.stopped


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


def test_prepare_voiced_show_builds_llm_client_by_default(monkeypatch):
    import llm_client
    import tts_client
    import revoice

    monkeypatch.setattr(tts_client, "build_tts_client", lambda config: object())
    monkeypatch.setattr(llm_client, "build_llm_client", lambda config: "real-client")
    captured = {}
    monkeypatch.setattr(revoice, "prepare_show", lambda script, llm, tts, workdir, **kw: captured.setdefault("llm", llm))

    replay.prepare_voiced_show(SCRIPT, {}, "/tmp/x")
    assert captured["llm"] == "real-client"


def test_prepare_voiced_show_skips_llm_when_env_set(monkeypatch):
    import llm_client
    import tts_client
    import revoice

    monkeypatch.setenv("REPLAY_SKIP_LLM", "1")
    monkeypatch.setattr(tts_client, "build_tts_client", lambda config: object())
    monkeypatch.setattr(llm_client, "build_llm_client",
                        lambda config: (_ for _ in ()).throw(AssertionError("should not be called")))
    captured = {}
    monkeypatch.setattr(revoice, "prepare_show", lambda script, llm, tts, workdir, **kw: captured.setdefault("llm", llm))

    replay.prepare_voiced_show(SCRIPT, {}, "/tmp/x")
    assert captured["llm"] is None
