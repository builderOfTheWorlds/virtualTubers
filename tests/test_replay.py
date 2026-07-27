"""Tests for app/replay.py -- display-only playback of validated episodes.

The replayer's contract: render every scene's render[] entries in order (or
interleaved under mode: parallel), route them to the right sink, never
execute recorded commands, and never crash the show over a bad entry or an
unwritable avatar state file. Rendering itself is delegated to
app/primitives.py -- these tests use the REAL coder-campaign primitive
table (config/primitives.yaml + config/campaigns/coder/primitives.yaml,
already covered by tests/test_primitives.py) so the glyphs/rates asserted
on here are the actual production ones, not a hand-rolled stand-in.
"""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import primitives as primitives_module  # noqa: E402
import replay  # noqa: E402
from replay import (  # noqa: E402
    MAX_SCENE_SCALE, MIN_SCENE_SCALE, Pacer, Palette, Performer,
    _truncate_lines, load_script,
)
from agent_state import read_state  # noqa: E402

PRIMITIVES = primitives_module.load_primitives("coder")


def make_performer(out, **kwargs):
    kwargs.setdefault("primitives", PRIMITIVES)
    return Performer(
        out=out,
        pacer=Pacer(enabled=False),
        palette=Palette(enabled=False),
        **kwargs,
    )


def boss_scene(text="Please add a heartbeat", **overrides):
    scene = {"speaker": "boss", "kind": "boss", "text": text, "fallback": "",
             "mode": "sequence",
             "render": [{"primitive": "show_boss_message", "payload": {"text": text}}]}
    scene.update(overrides)
    return scene


def coder_line_scene(text="On it, boss.\nStarting now.", name="KODI-7", **overrides):
    scene = {"speaker": "coder", "kind": "coder_talk", "text": text, "fallback": "",
             "mode": "sequence",
             "render": [{"primitive": "show_coder_line", "payload": {"text": text, "name": name}}]}
    scene.update(overrides)
    return scene


def read_scene(file="app/agent.py", **overrides):
    scene = {"speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
             "mode": "sequence",
             "render": [{"primitive": "show_read", "payload": {"file": file}}]}
    scene.update(overrides)
    return scene


def command_scene(command="git diff --stat", output="1 file changed", **overrides):
    scene = {"speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
             "mode": "sequence",
             "render": [{"primitive": "show_command",
                        "payload": {"command": command, "output": output}}]}
    scene.update(overrides)
    return scene


def diff_scene(file="app/agent.py", hunks=None, error=True, **overrides):
    hunks = hunks if hunks is not None else ["- i = 0", "+ i = 0  # tick"]
    scene = {"speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
             "mode": "sequence",
             "render": [{"primitive": "show_diff",
                        "payload": {"file": file, "hunks": hunks, "error": error}}]}
    scene.update(overrides)
    return scene


def tool_scene(tool="ScheduleWakeup", summary="delaySeconds=1500", **overrides):
    scene = {"speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
             "mode": "sequence",
             "render": [{"primitive": "show_tool", "payload": {"tool": tool, "summary": summary}}]}
    scene.update(overrides)
    return scene


EPISODE = {
    "meta": {"schema": 1, "campaign": "coder", "id": "2026-07-02_test",
             "title": "2026-07-02_test", "created": "2026-07-02T00:00:00Z"},
    "cast": ["boss", "coder"],
    "scenes": [
        boss_scene(),
        coder_line_scene(),
        read_scene(),
        command_scene(),
        diff_scene(),
        tool_scene(),
    ],
}


# -- deleted API: the per-event/per-tool rendering layer is gone --------------

def test_deleted_module_level_symbols_are_gone():
    """CONTRACT.md §6: estimate_event_seconds is deleted -- screen time is
    computed from a scene's render[] recipes now (primitives.estimate_scene_seconds)."""
    assert not hasattr(replay, "estimate_event_seconds")


def test_pacing_constants_moved_out_of_replay():
    """The rate constants used to live here; now every campaign's own
    config/campaigns/<campaign>/primitives.yaml carries them (CONTRACT.md
    §2 "the constants MUST come out of replay.py")."""
    for name in ("DIALOGUE_CPS", "CODE_CPS", "OUTPUT_LINES_PER_S", "EVENT_PAUSE_S", "TOOL_BEAT_S"):
        assert not hasattr(replay, name)


def test_deleted_performer_methods_are_gone():
    for name in ("_on_user_message", "_on_assistant_text", "_on_tool_call",
                "_perform_shell", "_perform_edit", "_perform_write",
                "_perform_read", "_perform_generic", "_perform_events",
                "_typed", "_paced_output"):
        assert not hasattr(Performer, name)


# -- basic rendering via the real coder recipes --------------------------------

def test_perform_renders_all_scenes_in_order():
    out = io.StringIO()
    make_performer(out).perform(EPISODE)
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


def test_perform_marks_errored_render_entry():
    out = io.StringIO()
    make_performer(out).perform(EPISODE)
    assert "✗ that didn't work" in out.getvalue()  # diff_scene's payload.error=True


def test_perform_respects_start_and_limit():
    out = io.StringIO()
    make_performer(out).perform(EPISODE, start=1, limit=1)
    text = out.getvalue()
    assert "On it, boss." in text
    assert "BOSS" not in text
    assert "git diff" not in text


def test_perform_unknown_primitive_logs_and_show_continues(caplog):
    """A bad render entry (e.g. a stale primitive name from a config that
    drifted) must never take the whole show down -- one entry failing is
    logged loudly and the rest of the show still airs (the "show must
    always air" rule, extended from LLM/TTS failures to rendering bugs)."""
    out = io.StringIO()
    episode = {
        "meta": {"title": "x"},
        "scenes": [
            {"speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
             "mode": "sequence",
             "render": [{"primitive": "no-such-primitive", "payload": {}}]},
            coder_line_scene(text="still here"),
        ],
    }
    with caplog.at_level("ERROR"):
        result = make_performer(out).perform(episode)
    text = out.getvalue()
    assert result is True
    assert "still here" in text
    assert "fin" in text
    assert any("no-such-primitive" in record.message for record in caplog.records)


def test_long_output_is_truncated_using_performers_own_default(tmp_path):
    """print_text (config/primitives.yaml, the shared layer) doesn't hard-
    code its own max_lines, so it falls through to Performer's
    max_output_lines default -- unlike the coder recipes below, which
    hardcode 24 for fidelity with the pre-campaign-platform show."""
    out = io.StringIO()
    body = "\n".join(f"line{i}" for i in range(60))
    episode = {"meta": {}, "scenes": [
        {"speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
         "mode": "sequence",
         "render": [{"primitive": "print_text", "payload": {"text": body}}]},
    ]}
    make_performer(out, max_output_lines=10).perform(episode)
    text = out.getvalue()
    assert "line9" in text
    assert "line10" not in text
    assert "(50 more lines)" in text


def test_show_command_truncates_at_recipe_hardcoded_24_lines():
    """CONTRACT.md §2's fidelity requirement: show_command's own recipe
    hardcodes max_lines: 24 (MAX_OUTPUT_LINES) regardless of what a
    Performer's own max_output_lines default is."""
    out = io.StringIO()
    body = "\n".join(f"line{i}" for i in range(60))
    scene = command_scene(command="ls", output=body)
    episode = {"meta": {}, "scenes": [scene]}
    make_performer(out, max_output_lines=5).perform(episode)
    text = out.getvalue()
    assert "line23" in text
    assert "line24" not in text
    assert "(36 more lines)" in text


def test_truncate_lines_no_op_under_limit():
    lines, hidden = _truncate_lines("a\nb", 5)
    assert lines == ["a", "b"] and hidden == 0


def test_pacer_disabled_types_full_text_instantly():
    chunks = []
    Pacer(enabled=False).type_out(chunks.append, "hello world", cps=1)
    assert "".join(chunks) == "hello world"


def test_default_sinks_route_everything_to_out():
    out = io.StringIO()
    performer = make_performer(out)
    assert performer.sinks == {"theater": out}


# -- speaker labeling moved to the generator's payload, not Performer ---------

def test_speaker_names_no_longer_drives_the_on_screen_label():
    """The on-screen speaker label used to come from Performer's own
    speaker_names-driven display-name resolution (via the now-deleted
    _on_assistant_text handler). It's baked into the render[] payload by
    the generator now (show_coder_line's payload.name) -- Performer just
    renders whatever the recipe says, regardless of speaker_names. This is
    the regression guard that the migration moved the responsibility
    rather than silently dropping multi-speaker labeling."""
    out = io.StringIO()
    performer = make_performer(out, speaker_names={"tester": "SHOULD-NOT-APPEAR"})
    show = [dict(coder_line_scene(text="hi", name="TESS-3"), speaker="tester",
                narration=None, audio=None)]
    performer.perform({"scenes": []}, show=show)
    text = out.getvalue()
    assert "TESS-3 ▸" in text
    assert "SHOULD-NOT-APPEAR" not in text
    assert performer._display_name == performer.worker_name  # reset after the scene


def test_display_name_bookkeeping_kept_for_backward_compat():
    """speaker_names/_resolve_display_name/_display_name stay on Performer
    (CONTRACT.md §6's frozen constructor shape) even though nothing in the
    new render pipeline reads self._display_name for output any more."""
    out = io.StringIO()
    performer = make_performer(out, speaker_names={"tester": "TESS-3"})
    assert performer._resolve_display_name("tester") == "TESS-3"
    assert performer._resolve_display_name("someone-else") == "someone-else"
    assert performer._resolve_display_name(None) == performer.worker_name


def test_avatar_state_written_and_show_survives_bad_path(tmp_path):
    # happy path: state file reflects the performance
    state_file = tmp_path / "agent_state.json"
    out = io.StringIO()
    make_performer(out, state_path=str(state_file)).perform(EPISODE)
    state = read_state(str(state_file))
    assert state["expression"] == "happy"  # final "fin" state

    # unwritable path: show must still complete
    out2 = io.StringIO()
    bad = tmp_path / "nope" / "deeper" / "state.json"
    make_performer(out2, state_path=str(bad)).perform(EPISODE)
    assert "fin" in out2.getvalue()


def test_perform_returns_true_on_natural_completion():
    out = io.StringIO()
    assert make_performer(out).perform(EPISODE) is True


# -- mode: parallel + target/sinks routing -------------------------------------

def test_mode_parallel_routes_entries_to_different_sinks():
    theater_out = io.StringIO()
    notes_out = io.StringIO()
    performer = Performer(out=theater_out, pacer=Pacer(enabled=False),
                          palette=Palette(enabled=False), primitives=PRIMITIVES,
                          sinks={"theater": theater_out, "notes": notes_out})
    scene = {
        "speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
        "mode": "parallel",
        "render": [
            {"primitive": "print_text", "payload": {"text": "on the main screen"}},
            {"primitive": "print_text", "target": "notes", "payload": {"text": "a side note"}},
        ],
    }
    performer.perform({"scenes": []}, show=[dict(scene, narration=None, audio=None)])
    assert "on the main screen" in theater_out.getvalue()
    assert "a side note" not in theater_out.getvalue()
    assert "a side note" in notes_out.getvalue()


def test_unwired_target_falls_back_to_theater_and_warns(caplog):
    """A VALID target (per the validator's unknown_target check at ingest)
    with no wired sink is the degraded case CONTRACT.md §6 describes --
    falls back to theater and logs a WARNING rather than inventing
    cross-pane IPC."""
    out = io.StringIO()
    performer = make_performer(out)  # sinks default to {"theater": out} only
    scene = {
        "speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
        "mode": "sequence",
        "render": [{"primitive": "print_text", "target": "nonexistent-pane",
                    "payload": {"text": "still shows up somewhere"}}],
    }
    with caplog.at_level("WARNING"):
        performer.perform({"scenes": []}, show=[dict(scene, narration=None, audio=None)])
    assert "still shows up somewhere" in out.getvalue()
    assert any("nonexistent-pane" in record.message for record in caplog.records)


def test_mode_parallel_one_entry_failing_does_not_abort_scene(caplog):
    out = io.StringIO()
    scene = {
        "speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
        "mode": "parallel",
        "render": [
            {"primitive": "no-such-primitive", "payload": {}},
            {"primitive": "print_text", "payload": {"text": "still rendered"}},
        ],
    }
    with caplog.at_level("ERROR"):
        result = make_performer(out).perform({"scenes": []}, show=[dict(scene, narration=None, audio=None)])
    text = out.getvalue()
    assert result is True
    assert "still rendered" in text
    assert "fin" in text


def test_sequence_mode_one_entry_failing_does_not_abort_scene(caplog):
    out = io.StringIO()
    scene = {
        "speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
        "mode": "sequence",
        "render": [
            {"primitive": "no-such-primitive", "payload": {}},
            {"primitive": "print_text", "payload": {"text": "still rendered"}},
        ],
    }
    with caplog.at_level("ERROR"):
        result = make_performer(out).perform({"scenes": []}, show=[dict(scene, narration=None, audio=None)])
    text = out.getvalue()
    assert result is True
    assert "still rendered" in text
    assert "fin" in text


def test_parallel_mode_replaystopped_on_background_thread_propagates(monkeypatch):
    """A ReplayStopped raised inside one of mode: parallel's background
    threads must still unwind the show -- threads don't propagate
    exceptions to the caller by default, so _perform_render_parallel has to
    capture and re-raise it after joining every thread."""
    calls = []

    def fake_perform_entry(entry, primitives, ctx):
        calls.append(entry["primitive"])
        if entry["primitive"] == "boom":
            raise replay.ReplayStopped()

    monkeypatch.setattr(replay, "perform_entry", fake_perform_entry)
    out = io.StringIO()
    performer = make_performer(out)
    scene = {
        "speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
        "mode": "parallel",
        "render": [{"primitive": "boom", "payload": {}}, {"primitive": "ok", "payload": {}}],
    }
    with pytest.raises(replay.ReplayStopped):
        performer._perform_render(scene)
    assert set(calls) == {"boom", "ok"}  # both threads ran before the stop propagated


def test_perform_stops_cleanly_when_parallel_scene_raises_replaystopped(monkeypatch):
    def fake_perform_entry(entry, primitives, ctx):
        if entry["primitive"] == "boom":
            raise replay.ReplayStopped()

    monkeypatch.setattr(replay, "perform_entry", fake_perform_entry)
    out = io.StringIO()
    performer = make_performer(out)
    show = [{"kind": "coder_work", "speaker": "coder", "narration": None, "audio": None,
            "mode": "parallel",
            "render": [{"primitive": "boom", "payload": {}}, {"primitive": "ok", "payload": {}}]}]
    result = performer.perform({"scenes": []}, show=show)
    assert result is False
    assert "stopped" in out.getvalue()


# -- audio-anchored scenes (voiced shows from revoice.prepare_show) ----------

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
    original = performer._perform_render

    def spy_perform_render(scene):
        scales.append(performer.pacer.scale)
        original(scene)

    performer._perform_render = spy_perform_render

    show = [
        dict(boss_scene(), narration="The boss wants a heartbeat.",
            audio=FakeNarrationAudio(duration=4.0)),
        dict(coder_line_scene(), narration="Time to get to work.", audio=None),
    ]
    performer.perform({"scenes": []}, show=show)
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
                          palette=Palette(enabled=False), primitives=PRIMITIVES)
    show = [dict(coder_line_scene(), narration="hi", audio=FakeNarrationAudio(duration=0.5))]
    performer.perform({"scenes": []}, show=show)
    assert waited == [0.5]  # held the scene for the spoken line


def test_perform_voiced_show_respects_start_and_limit():
    out = io.StringIO()
    show = [
        dict(boss_scene(), narration="one", audio=None),
        dict(coder_line_scene(), narration="two", audio=None),
        dict(command_scene(), narration="three", audio=None),
    ]
    make_performer(out).perform({"scenes": []}, show=show, start=1, limit=1)
    text = out.getvalue()
    assert "♪ two" in text
    assert "♪ one" not in text and "♪ three" not in text


# -- duet scenes: owned / target_duration (docs/duet_replay.md) ─────────────

def test_solo_show_unchanged_with_no_hooks_and_no_owned_key():
    """A scene dict with neither "owned" nor "target_duration" (today's
    prepare_show output) must behave identically to before -- this is the
    regression guard for the duet changes to _perform_scene/perform."""
    out = io.StringIO()
    performer = make_performer(out)
    show = [dict(boss_scene(), narration="The boss wants a heartbeat.", audio=None)]
    performer.perform({"scenes": []}, show=show)
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
    performer = Performer(out=out, pacer=Pacer(enabled=True), palette=Palette(enabled=False),
                          primitives=PRIMITIVES)
    show = [dict(coder_line_scene(), narration="Coder's take.", audio=None,
                owned=False, target_duration=5.0)]
    performer.perform({"scenes": []}, show=show)

    assert played == []  # never plays audio for a scene it doesn't own
    assert slept and max(slept) == pytest.approx(5.0, abs=0.2)


def test_owned_scene_without_audio_but_target_duration_also_holds(monkeypatch):
    """Spec: owned scenes fall into the target_duration path too when audio
    is None (e.g. a duet reuse that dropped this worker's own WAV)."""
    played = []
    monkeypatch.setattr(replay, "play_wav", lambda path, out=None: played.append(path))
    slept = []
    monkeypatch.setattr(replay.time, "sleep", lambda s: slept.append(s))

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(enabled=True), palette=Palette(enabled=False),
                          primitives=PRIMITIVES)
    show = [dict(coder_line_scene(), narration="Coder's take.", audio=None,
                owned=True, target_duration=3.0)]
    performer.perform({"scenes": []}, show=show)

    assert played == []
    assert slept and max(slept) == pytest.approx(3.0, abs=0.2)


def test_target_duration_hold_skipped_when_pacing_disabled():
    """--no-delay / fast-forward mode must not block on the hold."""
    out = io.StringIO()
    performer = make_performer(out)  # Pacer(enabled=False)
    show = [dict(coder_line_scene(), narration="fast", audio=None,
                owned=False, target_duration=50.0)]
    import time as real_time
    started = real_time.monotonic()
    performer.perform({"scenes": []}, show=show)
    assert real_time.monotonic() - started < 2.0  # did not actually wait 50s


def test_owned_scene_with_audio_plays_even_when_target_duration_present(monkeypatch):
    """Case A (owned + real audio) still wins over target_duration when both
    are present -- today's audio-anchored path is untouched."""
    playback = FakePlayback()
    monkeypatch.setattr(replay, "play_wav", lambda path, out=None: playback)
    waited = []
    monkeypatch.setattr(replay, "wait_extra",
                        lambda pb, started, min_seconds: waited.append(min_seconds))

    out = io.StringIO()
    performer = make_performer(out)
    show = [dict(boss_scene(), narration="hi", audio=FakeNarrationAudio(duration=2.0),
                owned=True, target_duration=999.0)]
    performer.perform({"scenes": []}, show=show)
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
    show = [dict(boss_scene(), narration="the boss speaks", audio=None,
                owned=False, target_duration=0.01)]
    performer.perform({"scenes": []}, show=show)
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
    show = [dict(boss_scene(), narration="the boss speaks", audio=None, owned=True)]
    performer.perform({"scenes": []}, show=show)
    assert ("speaking", "narrating the rerun", "the boss speaks") in calls


# -- duet hooks: on_scene_start / wait_for_scene (docs/duet_replay.md) ──────

def test_on_scene_start_called_once_per_scene_in_order_before_wait_for_scene():
    order = []
    show = [
        {"render": [], "narration": "s0", "audio": None},
        {"render": [], "narration": "s1", "audio": None},
    ]

    def on_scene_start(i):
        order.append(("start", i))

    def wait_for_scene(i):
        order.append(("wait", i))
        return i

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(enabled=False), palette=Palette(enabled=False),
                          on_scene_start=on_scene_start, wait_for_scene=wait_for_scene)
    performer.perform({"scenes": []}, show=show)
    assert order == [("start", 0), ("wait", 0), ("start", 1), ("wait", 1)]


def test_on_scene_start_exception_is_swallowed_and_show_continues():
    def blowing_up(i):
        raise RuntimeError("cue publish failed")

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(enabled=False), palette=Palette(enabled=False),
                          on_scene_start=blowing_up)
    show = [
        {"render": [], "narration": "s0", "audio": None},
        {"render": [], "narration": "s1", "audio": None},
    ]
    performer.perform({"scenes": []}, show=show)  # must not raise
    text = out.getvalue()
    assert "♪ s0" in text and "♪ s1" in text and "fin" in text


def test_wait_for_scene_proceed_matches_solo_output():
    """wait_for_scene(i) -> i every time (no jump) must render identically
    to the no-hook case."""
    show = [
        {"render": [], "narration": "s0", "audio": None},
        {"render": [], "narration": "s1", "audio": None},
    ]

    out_plain = io.StringIO()
    make_performer(out_plain).perform({"scenes": []}, show=[dict(s) for s in show])

    out_hooked = io.StringIO()
    performer = Performer(out=out_hooked, pacer=Pacer(enabled=False),
                          palette=Palette(enabled=False),
                          wait_for_scene=lambda i: i)
    performer.perform({"scenes": []}, show=[dict(s) for s in show])

    assert out_plain.getvalue() == out_hooked.getvalue()


def test_wait_for_scene_fast_forwards_when_two_or_more_behind():
    show = [
        {"render": [], "narration": "s0", "audio": None},
        {"render": [], "narration": "s1", "audio": None},
        {"render": [], "narration": "s2", "audio": None},
        {"render": [], "narration": "s3", "audio": None},
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
    performer.perform({"scenes": []}, show=show)

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
        {"render": [], "narration": "s0", "audio": None},
        {"render": [], "narration": "s1", "audio": None},
    ]
    ok = performer.perform({"scenes": []}, show=show)
    text = out.getvalue()
    assert ok is False
    assert "interrupted" in text
    assert "fin" not in text
    assert "♪ s0" not in text  # aborted before performing any scene
    assert calls[-1][0] == "idle"


# -- operator replay_stop (docs/operator_commands.md, docs/replay_pane.md) ──

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
    cleanly instead of raising out of perform() -- same shutdown shape as
    the existing wait_for_scene abort, but reachable from a solo show with
    no duet hooks at all. Uses the real coder recipes so should_stop's
    counter actually gets exercised via character-by-character typing."""
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 3

    out = io.StringIO()
    performer = Performer(out=out, pacer=Pacer(speed=1000.0, should_stop=should_stop),
                          palette=Palette(enabled=False), primitives=PRIMITIVES)
    avatar_calls = []
    original_avatar = performer._avatar

    def spy(expression, action="", bubble=None):
        avatar_calls.append((expression, action, bubble))
        original_avatar(expression, action=action, bubble=bubble)

    performer._avatar = spy
    ok = performer.perform(EPISODE)
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
                          palette=Palette(enabled=False), primitives=PRIMITIVES)
    show = [dict(boss_scene(), narration="hi", audio=FakeNarrationAudio(duration=5.0))]
    ok = performer.perform({"scenes": []}, show=show)
    assert ok is False
    assert playback.stopped


# -- load_script: delegates to episode_schema.load_episode -------------------

@pytest.fixture
def fake_episode_schema(monkeypatch):
    """app/episode_schema.py (CONTRACT.md §4) is being built by another
    agent in parallel with this work. If it's already importable, leave it
    alone -- real coverage of the actual validator is that module's own
    test suite's job. Otherwise install a minimal stand-in just so this
    test can verify replay.load_script's OWN plumbing: that it delegates to
    episode_schema.load_episode and returns whatever it returns."""
    import importlib.util
    if importlib.util.find_spec("episode_schema") is not None:
        yield
        return

    import types

    fake = types.ModuleType("episode_schema")

    class EpisodeError(Exception):
        pass

    def load_episode(path, *, primitives=None, rules=None, pane_ids=None,
                     assets_dir=None, campaign=None):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    fake.EpisodeError = EpisodeError
    fake.load_episode = load_episode
    monkeypatch.setitem(sys.modules, "episode_schema", fake)
    yield


def test_load_script_delegates_to_episode_schema_load_episode(tmp_path, fake_episode_schema):
    p = tmp_path / "ep.json"
    p.write_text(json.dumps(EPISODE), encoding="utf-8")
    result = load_script(p)
    assert result["meta"]["id"] == "2026-07-02_test"


# -- prepare_voiced_show glue --------------------------------------------------

def test_prepare_voiced_show_builds_llm_client_by_default(monkeypatch):
    import llm_client
    import tts_client
    import revoice

    monkeypatch.setattr(tts_client, "build_tts_client", lambda config: object())
    monkeypatch.setattr(llm_client, "build_llm_client", lambda config: "real-client")
    captured = {}
    monkeypatch.setattr(revoice, "prepare_show", lambda episode, llm, tts, workdir, **kw: captured.setdefault("llm", llm))

    replay.prepare_voiced_show(EPISODE, {}, "/tmp/x")
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
    monkeypatch.setattr(revoice, "prepare_show", lambda episode, llm, tts, workdir, **kw: captured.setdefault("llm", llm))

    replay.prepare_voiced_show(EPISODE, {}, "/tmp/x")
    assert captured["llm"] is None


def test_prepare_voiced_show_loads_campaign_primitives_and_narration(monkeypatch):
    """Necessary ripple from revoice.prepare_show now requiring primitives/
    narration_config: prepare_voiced_show must load and thread them through
    for whichever campaign it's given (default "coder")."""
    import llm_client
    import tts_client
    import revoice

    monkeypatch.setattr(tts_client, "build_tts_client", lambda config: object())
    monkeypatch.setattr(llm_client, "build_llm_client", lambda config: None)
    captured = {}

    def fake_prepare_show(episode, llm, tts, workdir, **kw):
        captured.update(kw)

    monkeypatch.setattr(revoice, "prepare_show", fake_prepare_show)
    replay.prepare_voiced_show(EPISODE, {}, "/tmp/x", campaign="coder")
    assert "show_command" in captured["primitives"]
    assert "boss" in captured["narration_config"]
