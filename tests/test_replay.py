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

from replay import Pacer, Palette, Performer, load_script, _truncate_lines  # noqa: E402
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
