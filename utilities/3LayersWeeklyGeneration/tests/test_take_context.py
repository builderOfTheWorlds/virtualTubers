"""Take-level fixes from the 2026-09-29 full-day review.

That run's takes ignored their slots: the 00:00-06:00 off-hours slot planned
for the Party Member and Office Manager came back as a CEO-led standup, and a
ship slot came back as a model refusal voiced by the CEO. These tests pin the
prompt context, the participant filter, and the refusal rejection.
"""
import generate_segment_dialogue as gsd
import worklist
from segment_helpers import ambient_slot, segment_config  # noqa: F401
from test_generate_segment_dialogue import FakeBeat, RecordingImproviser, config  # noqa: F401


def _unit(slot):
    return worklist.WorkUnit(segment_id="seg-001", slot_id=slot["slot_id"], take=1,
                             conditions={}, path=None, slot=slot)


def test_take_prompt_names_the_participants_and_day_context():
    slot = ambient_slot(1, prompt="The office is dark.", participants=["chadwick"])
    prompt = gsd.build_take_prompt(slot, {"clock": "00:00-06:00 (off-hours)",
                                          "synopsis": "Nobody is in yet."})
    assert prompt.startswith("The office is dark.")
    assert "Only these cast members are present and may speak in this scene: chadwick." in prompt
    assert "Time of day: 00:00-06:00 (off-hours)." in prompt
    assert "Nobody is in yet." in prompt


def test_take_prompt_without_a_brief_adds_only_the_participants():
    slot = ambient_slot(1, prompt="P.", participants=["Leena"])
    assert gsd.build_take_prompt(slot, None) == (
        "P.\nOnly these cast members are present and may speak in this scene: Leena. "
        "Nobody else speaks.")


def test_lines_from_speakers_outside_the_slot_are_dropped(config):
    slot = ambient_slot(1, participants=["Leena"])
    beats = [FakeBeat("Leena", "Here.", kind="dialogue"),
             FakeBeat("chadwick", "Not here.", kind="dialogue"),
             FakeBeat("gm", "Wind outside.", kind="narration")]
    out = gsd.generate_take(RecordingImproviser(beats=beats), _unit(slot), config)
    assert [b["speaker"] for b in out] == ["Leena", "gm"]


def test_a_refusal_rejects_the_whole_take(config):
    beats = [FakeBeat("Leena", "Good morning.", kind="dialogue"),
             FakeBeat("Leena", "I will not generate or roleplay that type of content.",
                      kind="dialogue")]
    out = gsd.generate_take(RecordingImproviser(beats=beats), _unit(ambient_slot(1)), config)
    assert out == []


def test_ordinary_dialogue_is_not_mistaken_for_a_refusal():
    beats = [FakeBeat("Leena", "I can't find the build log."),
             FakeBeat("Leena", "Sorry, the test will not run until noon.")]
    assert gsd.is_refusal(beats) is False


def test_a_silent_cast_members_line_becomes_gm_narration(config):
    class Pack:
        gm_id = "gm"

    improviser = RecordingImproviser(beats=[
        FakeBeat("Leena", "You're a saint.", kind="dialogue"),
        FakeBeat("chadwick", "Coffee?", kind="dialogue")])
    improviser.pack = Pack()
    slot = ambient_slot(1, participants=["Leena", "chadwick"])
    out = gsd.generate_take(improviser, _unit(slot), config, silent={"Leena"})
    assert out[0] == {"kind": "narration", "speaker": "gm", "text": "You're a saint."}
    assert out[1]["speaker"] == "chadwick" and out[1]["kind"] == "dialogue"


def test_take_prompt_tells_the_model_who_is_silent():
    slot = ambient_slot(1, prompt="P.", participants=["Leena", "chadwick"])
    prompt = gsd.build_take_prompt(slot, None, silent={"Leena"})
    assert "Never write a spoken line for Leena" in prompt
    assert "narration only" not in prompt


def test_a_scene_with_only_silent_cast_is_narration_only():
    slot = ambient_slot(1, prompt="P.", participants=["Leena"])
    assert "narration only" in gsd.build_take_prompt(slot, None, silent={"Leena"})


def test_silent_cast_ids_reads_the_pack_speech_field(tmp_path):
    (tmp_path / "cast").mkdir()
    (tmp_path / "cast" / "pm.yaml").write_text("speech: >-\n  None. He never speaks.\n")
    (tmp_path / "cast" / "ceo.yaml").write_text("speech: Short, clipped.\n")

    class Pack:
        root = tmp_path
        cast = {"pm": None, "ceo": None, "missing": None}

    assert gsd.silent_cast_ids(Pack()) == {"pm"}
