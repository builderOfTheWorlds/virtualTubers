"""Tests for app/table/story_log.py (the GM/seat "what has already happened" memory).

Plan: .claude/prompts/ashiorid_office_loop_fix_plan.md P1.
"""
import pytest

from pending import require

story_log = require("table.story_log", "app/table/story_log.py", wp="office-loop P1")


def _state(scene_id="s1", notes="Nora arrives and sets up.", delta=None, resolved=True):
    return {
        "scene_id": scene_id,
        "resolved": resolved,
        "state_delta": dict(delta or {"om": "set up"}),
        "notes": notes,
        "transcript": [
            {"speaker": "gm", "text": "The lights hum on.", "kind": "direction"},
            {"speaker": "tuber_6", "text": "Morning. Three things, then I'll let you go.", "kind": "reply"},
            {"speaker": "tuber_6", "text": "Coffee is on.", "kind": "overrule"},
            {"speaker": "gm", "text": "She checks the post tray.", "kind": "direction"},
        ],
    }


def _contract(scene_id="s1", goal="The Office Manager arrives."):
    return {"scene_id": scene_id, "canon_goal": goal}


# ---------------------------------------------------------------- StoryEntry
def test_entry_from_state_splits_directions_and_lines():
    e = story_log.entry_from_state(_state(), _contract())
    assert e.scene_id == "s1"
    assert e.goal == "The Office Manager arrives."
    assert e.notes == "Nora arrives and sets up."
    assert e.state_delta == {"om": "set up"}
    assert e.resolved is True and e.skipped is False
    assert e.directions == ("The lights hum on.", "She checks the post tray.")
    assert e.lines == ("Morning. Three things, then I'll let you go.", "Coffee is on.")


def test_entry_from_state_missing_notes_and_transcript_is_safe():
    e = story_log.entry_from_state({"scene_id": "s9"}, {"scene_id": "s9"})
    assert e.scene_id == "s9" and e.goal == "" and e.notes == ""
    assert e.lines == () and e.directions == () and e.state_delta == {}


def test_entry_from_state_skipped_flag_and_max_lines():
    st = _state()
    st["transcript"] = [{"speaker": "tuber_1", "text": f"line {i}", "kind": "reply"} for i in range(30)]
    e = story_log.entry_from_state(st, _contract(), skipped=True, max_lines=5)
    assert e.skipped is True
    assert e.lines == tuple(f"line {i}" for i in range(25, 30))


def test_entry_roundtrip_dict():
    e = story_log.entry_from_state(_state(), _contract())
    d = e.to_dict()
    assert isinstance(d["lines"], list) and isinstance(d["directions"], list)
    assert story_log.StoryEntry.from_dict(d) == e


def test_entry_from_dict_tolerates_unknown_and_missing_keys():
    e = story_log.StoryEntry.from_dict({"scene_id": "x", "bogus": 1})
    assert e.scene_id == "x" and e.lines == () and e.resolved is True


@pytest.mark.parametrize("bad", [None, "x", 3, {}, {"scene_id": ""}])
def test_entry_from_dict_rejects_bad(bad):
    with pytest.raises(ValueError):
        story_log.StoryEntry.from_dict(bad)


# ---------------------------------------------------------------- StoryLog
def _entry(i, **kw):
    return story_log.StoryEntry(scene_id=f"s{i}", goal=f"goal {i}", notes=f"notes {i}",
                                **kw)


def test_log_keeps_only_last_keep_entries():
    log = story_log.StoryLog(keep=3)
    for i in range(5):
        log.append(_entry(i))
    assert len(log) == 3
    assert [e.scene_id for e in log.entries] == ["s2", "s3", "s4"]


def test_log_clear():
    log = story_log.StoryLog([_entry(1)])
    log.clear()
    assert len(log) == 0 and log.render() == ""


def test_log_to_list_from_list_roundtrip_and_skips_bad_items():
    log = story_log.StoryLog([_entry(1), _entry(2, skipped=True)])
    items = log.to_list()
    again = story_log.StoryLog.from_list(items + [None, {"scene_id": ""}, "junk"], keep=12)
    assert again.entries == log.entries


def test_from_list_none_is_empty():
    assert len(story_log.StoryLog.from_list(None)) == 0


def test_recent_lines_and_directions_skip_skipped_entries():
    log = story_log.StoryLog([
        _entry(1, lines=("a1",), directions=("d1",)),
        _entry(2, lines=("a2",), directions=("d2",)),
        _entry(3, lines=("a3",), directions=("d3",), skipped=True),
        _entry(4, lines=("a4", "b4"), directions=("d4",)),
    ])
    assert log.recent_lines(2) == ("a2", "a4", "b4")
    assert log.recent_directions(2) == ("d2", "d4")
    assert log.recent_lines(0) == ()


# ---------------------------------------------------------------- render
def test_render_empty_is_empty_string():
    assert story_log.StoryLog().render() == ""


def test_render_heading_order_and_content():
    log = story_log.StoryLog([
        _entry(1, state_delta={"om": "arrived"}),
        _entry(2),
        _entry(3, skipped=True),
    ])
    text = log.render()
    assert text.startswith(story_log.HEADING + "\n")
    assert text.index("[s1]") < text.index("[s2]") < text.index("[s3]")
    assert "goal 1" in text and "notes 1" in text and "om=arrived" in text
    s3_line = [ln for ln in text.splitlines() if "[s3]" in ln][0]
    assert "skipped" in s3_line and "goal 3" not in s3_line


def test_render_max_entries_keeps_newest():
    log = story_log.StoryLog([_entry(i) for i in range(10)])
    text = log.render(max_entries=3)
    assert "[s9]" in text and "[s7]" in text and "[s6]" not in text


@pytest.mark.parametrize("cap", [60, 120, 300, 2500])
def test_render_never_exceeds_max_chars_and_drops_oldest(cap):
    log = story_log.StoryLog([story_log.StoryEntry(scene_id=f"s{i}", goal="word " * 40,
                                                   notes="note " * 20) for i in range(6)])
    text = log.render(max_entries=6, max_chars=cap)
    assert len(text) <= cap
    if text:
        assert text.startswith(story_log.HEADING)
        assert "[s5]" in text          # the newest entry survives (possibly shortened)


def test_render_cap_below_heading_returns_empty():
    log = story_log.StoryLog([_entry(1)])
    assert log.render(max_chars=5) == ""
