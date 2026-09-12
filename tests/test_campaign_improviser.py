"""Tests for app/campaign/improviser.py — the seam where a persona LLM riffs.

This module is what turns a finite script into 24/7 content: it takes a beat's
scripted line as *intent* and returns a fresh phrasing in the speaker's voice,
and it generates whole ambient scenes from a prompt.

Two contracts live here and they deliberately differ:

  __call__        RAISES ImproviserError on any failure. The renderer already
                  catches everything and falls back to the scripted line, so a
                  failure costs one line's freshness and nothing else.
  generate_scene  NEVER raises. Returns [] on any failure, and the runtime
                  skips the ambient scene. An LLM outage costs filler, not air.

No test in this file touches the network. Every LLM is a fake object exposing
`complete(system_prompt, messages)`, matching app/llm_client.py's interface.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from campaign.improviser import ImproviserError, LLMImproviser  # noqa: E402
from campaign.pack import Beat, CampaignPack, CastMember, Scene  # noqa: E402


# ── fakes and fixtures ───────────────────────────────────────────────────────
class FakeLLM:
    """Records every call and returns canned replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies) or ["A fresh line."]
        self.calls = []

    def complete(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0]

    @property
    def last_system(self):
        return self.calls[-1][0]

    @property
    def last_user(self):
        return "\n".join(message["content"] for message in self.calls[-1][1])


class ExplodingLLM:
    def __init__(self, exc=None):
        self.exc = exc or RuntimeError("model is on fire")

    def complete(self, system_prompt, messages):
        raise self.exc


def make_pack(scenes=(), lore=None):
    cast = {
        "gm": CastMember(id="gm", name="The Chronicler", role="gm",
                         archetype="narrator", system_prompt="You narrate coldly."),
        "Leena": CastMember(id="Leena", name="Leena", role="player",
                            archetype="wizard", system_prompt="You are precise and tired."),
        "chadwick": CastMember(id="chadwick", name="Chadwick", role="player",
                              archetype="fighter"),
    }
    return CampaignPack(
        name="testpack", title="Test", genre="fantasy", start_scene="opening",
        gm_id="gm", player_ids=["Leena", "chadwick"], primitives=[],
        theme={}, cast=cast, scenes={s.id: s for s in scenes},
        root=Path("/nonexistent"), lore_dir=None, lore=lore or {},
    )


def dialogue(text="We should run.", speaker="Leena"):
    return Beat(kind="dialogue", speaker=speaker, text=text, improv=True)


@pytest.fixture
def Leena():
    return make_pack().cast["Leena"]


def build(llm=None, pack=None, **kwargs):
    return LLMImproviser(pack or make_pack(), llm=llm if llm is not None else FakeLLM(),
                         **kwargs)


# ── construction and defaults ────────────────────────────────────────────────
def test_improviser_constructs_with_only_a_pack():
    improviser = LLMImproviser(make_pack())

    assert improviser.pack is not None
    assert improviser.llm is None
    assert improviser.loop == 1
    assert improviser.carry == {}
    assert improviser.recent == []


def test_defaults_are_the_documented_ones():
    improviser = LLMImproviser(make_pack())

    assert improviser.max_recent == 8
    assert improviser.max_words == 45


# ── the renderer seam ────────────────────────────────────────────────────────
def test_call_returns_the_models_line(Leena):
    improviser = build(FakeLLM("The door is not the problem."))

    assert improviser(dialogue(), Leena) == "The door is not the problem."


def test_call_without_an_llm_raises(Leena):
    improviser = LLMImproviser(make_pack(), llm=None)

    with pytest.raises(ImproviserError):
        improviser(dialogue(), Leena)


def test_a_raising_llm_becomes_an_improviser_error(Leena):
    improviser = build(ExplodingLLM())

    with pytest.raises(ImproviserError):
        improviser(dialogue(), Leena)


def test_the_llm_is_called_exactly_once_per_beat(Leena):
    llm = FakeLLM("Line.")
    improviser = build(llm)

    improviser(dialogue(), Leena)

    assert len(llm.calls) == 1


# ── prompt assembly ──────────────────────────────────────────────────────────
def test_system_prompt_carries_the_personas_own_prompt(Leena):
    llm = FakeLLM()
    build(llm)(dialogue(), Leena)

    assert "You are precise and tired." in llm.last_system


def test_system_prompt_names_the_character_and_archetype(Leena):
    llm = FakeLLM()
    build(llm)(dialogue(), Leena)

    assert "Leena" in llm.last_system
    assert "wizard" in llm.last_system


def test_a_persona_without_a_system_prompt_still_works():
    pack = make_pack()
    llm = FakeLLM()

    result = build(llm, pack=pack)(dialogue(speaker="chadwick"), pack.cast["chadwick"])

    assert result
    assert "Chadwick" in llm.last_system


def test_the_scripted_line_is_passed_as_intent(Leena):
    llm = FakeLLM()
    build(llm)(dialogue("We should run."), Leena)

    assert "We should run." in llm.last_user


def test_a_beat_with_no_scripted_text_still_produces_a_prompt(Leena):
    llm = FakeLLM()

    assert build(llm)(Beat(kind="dialogue", speaker="Leena", improv=True), Leena)


def test_the_word_budget_is_stated_to_the_model(Leena):
    llm = FakeLLM()
    build(llm, max_words=20)(dialogue(), Leena)

    assert "20" in llm.last_user or "20" in llm.last_system


# ── scene and lore context ───────────────────────────────────────────────────
def test_scene_lore_reaches_the_model(Leena):
    scene = Scene(id="opening", lore=["the-event"])
    pack = make_pack(scenes=[scene], lore={"the-event": "The sky broke in 1042.",
                                           "moonwells": "Unrelated."})
    llm = FakeLLM()
    improviser = build(llm, pack=pack)
    improviser.update_context(scene=scene)

    improviser(dialogue(), Leena)

    assert "The sky broke in 1042." in llm.last_user
    # only the selected note travels — context is a budget, not a dump
    assert "Unrelated." not in llm.last_user


def test_a_scene_selecting_no_lore_sends_none(Leena):
    scene = Scene(id="opening")
    pack = make_pack(scenes=[scene], lore={"the-event": "The sky broke in 1042."})
    llm = FakeLLM()
    improviser = build(llm, pack=pack)
    improviser.update_context(scene=scene)

    improviser(dialogue(), Leena)

    assert "The sky broke in 1042." not in llm.last_user


def test_a_lore_selector_naming_a_missing_note_is_skipped_not_fatal(Leena):
    scene = Scene(id="opening", lore=["ghost-note"])
    pack = make_pack(scenes=[scene])
    improviser = build(FakeLLM(), pack=pack)
    improviser.update_context(scene=scene)

    assert improviser(dialogue(), Leena)


def test_the_scene_title_reaches_the_model(Leena):
    scene = Scene(id="opening", title="The Invitation")
    llm = FakeLLM()
    improviser = build(llm, pack=make_pack(scenes=[scene]))
    improviser.update_context(scene=scene)

    improviser(dialogue(), Leena)

    assert "The Invitation" in llm.last_user


# ── the rolling transcript window ────────────────────────────────────────────
def test_observed_lines_reach_the_next_prompt(Leena):
    llm = FakeLLM()
    improviser = build(llm)

    improviser.observe("Chadwick", "The door is barred.")
    improviser(dialogue(), Leena)

    assert "Chadwick" in llm.last_user
    assert "The door is barred." in llm.last_user


def test_the_window_is_capped_at_max_recent(Leena):
    llm = FakeLLM()
    improviser = build(llm, max_recent=3)

    for index in range(10):
        improviser.observe("Chadwick", f"line number {index}")
    improviser(dialogue(), Leena)

    assert len(improviser.recent) == 3
    assert "line number 9" in llm.last_user
    assert "line number 0" not in llm.last_user


def test_empty_observations_are_ignored():
    improviser = build()

    improviser.observe("Chadwick", "")
    improviser.observe("Chadwick", "   ")
    improviser.observe("Chadwick", None)

    assert improviser.recent == []


def test_the_window_survives_a_scene_change(Leena):
    llm = FakeLLM()
    improviser = build(llm)

    improviser.observe("Chadwick", "The door is barred.")
    improviser.update_context(scene=Scene(id="next-scene"))
    improviser(dialogue(), Leena)

    # continuity across a scene boundary is the entire point of the window
    assert "The door is barred." in llm.last_user


# ── memory across loops ──────────────────────────────────────────────────────
def test_loop_one_with_empty_carry_mentions_no_memory(Leena):
    llm = FakeLLM()
    build(llm)(dialogue(), Leena)

    assert "loops_completed" not in llm.last_user


def test_a_later_loop_tells_the_model_it_has_been_here_before(Leena):
    llm = FakeLLM()
    improviser = build(llm)
    improviser.update_context(loop=7, carry={"loops_completed": 6})

    improviser(dialogue(), Leena)

    assert "7" in llm.last_user


def test_carry_contents_reach_the_model(Leena):
    llm = FakeLLM()
    improviser = build(llm)
    improviser.update_context(loop=3, carry={"loops_completed": 2,
                                             "visited": ["letos-manor"]})

    improviser(dialogue(), Leena)

    assert "letos-manor" in llm.last_user


def test_update_context_leaves_unspecified_fields_alone():
    improviser = build()
    improviser.update_context(loop=4, carry={"a": 1})

    improviser.update_context(scene=Scene(id="s"))

    assert improviser.loop == 4
    assert improviser.carry == {"a": 1}


# ── sanitation: what a small model actually returns ──────────────────────────
# llama3.1:8b will label its own line, wrap it in quotes, add stage directions
# and run long. Every one of these reaches TTS verbatim if it is not stripped,
# and a character announcing its own name aloud is the loudest possible bug.
@pytest.mark.parametrize("raw, expected", [
    ("Leena: We should run.", "We should run."),
    ("Leena: We should run.", "We should run."),
    ("Leena: We should run.", "We should run."),
    ("Leena:We should run.", "We should run."),
    ("  Leena:   We should run.  ", "We should run."),
])
def test_a_leading_name_label_is_stripped(Leena, raw, expected):
    assert build(FakeLLM(raw))(dialogue(), Leena) == expected


def test_a_label_that_is_not_the_speakers_name_is_kept(Leena):
    # "Note:" is content, not a speaker label — stripping it would eat the line.
    assert build(FakeLLM("Note: the door is barred."))(dialogue(), Leena) \
        == "Note: the door is barred."


@pytest.mark.parametrize("raw", [
    '"We should run."',
    "'We should run.'",
    '“We should run.”',
    '"We should run."  ',
])
def test_wrapping_quotes_are_stripped(Leena, raw):
    assert build(FakeLLM(raw))(dialogue(), Leena) == "We should run."


def test_an_internal_quote_is_left_alone(Leena):
    raw = 'He said "run" and I ran.'

    assert build(FakeLLM(raw))(dialogue(), Leena) == raw


@pytest.mark.parametrize("raw", [
    "*leans on the table* We should run.",
    "We should run. *stands up*",
    "*sighs* We should run. *waits*",
])
def test_stage_directions_are_stripped(Leena, raw):
    assert build(FakeLLM(raw))(dialogue(), Leena) == "We should run."


def test_markdown_emphasis_markers_are_stripped(Leena):
    assert build(FakeLLM("We **should** run."))(dialogue(), Leena) == "We should run."


def test_newlines_collapse_to_one_paragraph(Leena):
    result = build(FakeLLM("We should run.\n\nThe door is barred."))(dialogue(), Leena)

    assert "\n" not in result
    assert result == "We should run. The door is barred."


def test_repeated_whitespace_collapses(Leena):
    assert build(FakeLLM("We    should\t\trun."))(dialogue(), Leena) == "We should run."


def test_output_is_capped_at_max_words(Leena):
    raw = " ".join(["word"] * 200)
    result = build(FakeLLM(raw), max_words=10)(dialogue(), Leena)

    assert len(result.split()) == 10


def test_a_truncated_line_still_ends_in_terminal_punctuation(Leena):
    raw = " ".join(["word"] * 200)
    result = build(FakeLLM(raw), max_words=10)(dialogue(), Leena)

    assert result.endswith(".")


def test_a_short_line_is_not_padded_or_truncated(Leena):
    assert build(FakeLLM("No."), max_words=45)(dialogue(), Leena) == "No."


@pytest.mark.parametrize("terminal", [".", "!", "?", "…"])
def test_truncation_does_not_double_up_existing_terminal_punctuation(Leena, terminal):
    # The cut can land on a word that already ends a sentence. Appending a
    # second period there ships "four.." and "four!." straight to text-to-speech.
    raw = " ".join(["word", "word", "word", "word" + terminal, "word", "word"])
    result = build(FakeLLM(raw), max_words=4)(dialogue(), Leena)

    assert result == "word word word word" + terminal
    assert len(result.split()) == 4


def test_truncation_onto_a_comma_replaces_it_rather_than_appending(Leena):
    raw = " ".join(["word", "word", "word", "word,", "word", "word"])

    assert build(FakeLLM(raw), max_words=4)(dialogue(), Leena) == "word word word word."


@pytest.mark.parametrize("raw", ["", "   ", "\n\n", "*shrugs*", '""'])
def test_an_empty_or_contentless_reply_raises(Leena, raw):
    with pytest.raises(ImproviserError):
        build(FakeLLM(raw))(dialogue(), Leena)


def test_a_non_string_reply_raises(Leena):
    with pytest.raises(ImproviserError):
        build(FakeLLM(None))(dialogue(), Leena)


def test_sanitation_survives_every_defect_at_once(Leena):
    raw = '  Leena: *leans in* "We **should** run.\n\nNow."  '

    assert build(FakeLLM(raw))(dialogue(), Leena) == "We should run. Now."


# ── generating a whole ambient scene ─────────────────────────────────────────
AMBIENT = Scene(id="camp-fire", ambient=True, title="Camp",
                prompt="The party waits out a rainstorm. Nothing happens.")


def test_generate_scene_returns_beats():
    llm = FakeLLM("Leena: The rain has not stopped.\n"
                  "chadwick: It will.\n")
    beats = build(llm).generate_scene(AMBIENT)

    assert len(beats) == 2
    assert all(isinstance(beat, Beat) for beat in beats)
    assert [beat.speaker for beat in beats] == ["Leena", "chadwick"]
    assert beats[0].text == "The rain has not stopped."


def test_generated_beats_are_dialogue_and_not_marked_improv():
    llm = FakeLLM("Leena: The rain has not stopped.")
    beat = build(llm).generate_scene(AMBIENT)[0]

    # already generated — re-improvising them would double the model calls
    assert beat.kind == "dialogue"
    assert beat.improv is False


def test_generated_beats_carry_the_scene_prompt_to_the_model():
    llm = FakeLLM("Leena: Rain.")
    build(llm).generate_scene(AMBIENT)

    assert "The party waits out a rainstorm." in llm.last_user


def test_an_unprefixed_line_becomes_gm_narration():
    llm = FakeLLM("The fire gutters and holds.")
    beat = build(llm).generate_scene(AMBIENT)[0]

    assert beat.kind == "narration"
    assert beat.speaker == "gm"
    assert beat.text == "The fire gutters and holds."


def test_a_line_naming_an_unknown_speaker_becomes_narration():
    llm = FakeLLM("mallory: I was never cast.")
    beat = build(llm).generate_scene(AMBIENT)[0]

    assert beat.kind == "narration"
    assert beat.speaker == "gm"


def test_generated_beat_text_is_sanitized():
    llm = FakeLLM('Leena: *sighs* "The **rain** has not stopped."')
    beat = build(llm).generate_scene(AMBIENT)[0]

    assert beat.text == "The rain has not stopped."


def test_generated_beats_populate_the_variant_pool():
    llm = FakeLLM("Leena: Rain.")
    beat = build(llm).generate_scene(AMBIENT)[0]

    assert beat.texts == ["Rain."]


def test_generated_beats_carry_a_key_naming_the_scene():
    llm = FakeLLM("Leena: Rain.\nchadwick: Still.")
    beats = build(llm).generate_scene(AMBIENT)

    assert all(beat.key.startswith("camp-fire#") for beat in beats)
    assert len({beat.key for beat in beats}) == 2


def test_blank_lines_are_skipped():
    llm = FakeLLM("Leena: Rain.\n\n\nchadwick: Still.\n")

    assert len(build(llm).generate_scene(AMBIENT)) == 2


def test_generate_scene_is_capped_at_max_beats():
    llm = FakeLLM("\n".join(f"Leena: line {i}." for i in range(50)))
    beats = build(llm, max_beats=6).generate_scene(AMBIENT)

    assert len(beats) == 6


def test_max_beats_counts_emitted_beats_not_scanned_lines():
    # Blank and contentless lines are free — the cap is a budget on the scene's
    # length, not on how much of the reply we are willing to read.
    llm = FakeLLM("\n".join(f"Leena: line {i}." if i % 2 else "" for i in range(40)))

    assert len(build(llm, max_beats=6).generate_scene(AMBIENT)) == 6


def test_beat_keys_index_the_returned_list_not_the_reply_lines():
    # Skipped lines must not punch holes in the numbering: the renderer's
    # variant cycling keys off beat.key, and "#gen2" as the first beat of a
    # scene is a lie about position that survives into every later loop.
    llm = FakeLLM("\n\nLeena: Rain.\n\n\nchadwick: Still.\n")
    beats = build(llm).generate_scene(AMBIENT)

    assert [beat.key for beat in beats] == ["camp-fire#gen0", "camp-fire#gen1"]


@pytest.mark.parametrize("reply", [
    "Leena: *shrugs*",           # the single most common small-model tic
    "Leena:",
    "*shrugs*",
    'Leena: ""',
    "Leena: Rain.\n*shrugs*\nchadwick: Cold.",
])
def test_a_line_that_sanitizes_to_nothing_is_skipped_not_raised(reply):
    # Sanitation raises by design, but that contract belongs to __call__ alone.
    # Letting it escape here would take the stream down over a stage direction.
    beats = build(FakeLLM(reply)).generate_scene(AMBIENT)

    assert all(beat.text for beat in beats)


def test_contentless_lines_do_not_discard_the_usable_ones():
    llm = FakeLLM("Leena: Rain.\n*shrugs*\nchadwick: Cold.")
    beats = build(llm).generate_scene(AMBIENT)

    assert [beat.text for beat in beats] == ["Rain.", "Cold."]
    assert [beat.key for beat in beats] == ["camp-fire#gen0", "camp-fire#gen1"]


def test_generate_scene_returns_empty_when_no_line_survives_sanitation():
    assert build(FakeLLM("*shrugs*\n*nods*\n")).generate_scene(AMBIENT) == []


# generate_scene NEVER raises — the runtime skips the scene on an empty return.
def test_generate_scene_returns_empty_without_an_llm():
    improviser = LLMImproviser(make_pack(), llm=None)

    assert improviser.generate_scene(AMBIENT) == []


def test_generate_scene_returns_empty_when_the_llm_raises():
    assert build(ExplodingLLM()).generate_scene(AMBIENT) == []


def test_generate_scene_returns_empty_for_a_scene_with_no_prompt():
    assert build(FakeLLM("Leena: Rain.")).generate_scene(
        Scene(id="camp-fire", ambient=True)) == []


@pytest.mark.parametrize("raw", ["", "   ", "\n\n"])
def test_generate_scene_returns_empty_for_an_empty_reply(raw):
    assert build(FakeLLM(raw)).generate_scene(AMBIENT) == []


def test_generate_scene_returns_empty_for_a_non_string_reply():
    assert build(FakeLLM(None)).generate_scene(AMBIENT) == []


def test_generate_scene_sends_the_cast_roster():
    llm = FakeLLM("Leena: Rain.")
    build(llm).generate_scene(AMBIENT)

    assert "Leena" in llm.last_user
    assert "chadwick" in llm.last_user


def test_generate_scene_includes_scene_lore():
    scene = Scene(id="camp-fire", ambient=True, prompt="They wait.",
                  lore=["the-event"])
    pack = make_pack(scenes=[scene], lore={"the-event": "The sky broke in 1042."})
    llm = FakeLLM("Leena: Rain.")

    build(llm, pack=pack).generate_scene(scene)

    assert "The sky broke in 1042." in llm.last_user


# ── logging ──────────────────────────────────────────────────────────────────
def test_a_generated_line_is_logged_at_debug(Leena, caplog):
    with caplog.at_level("DEBUG", logger="campaign.improviser"):
        build(FakeLLM("A fresh line."))(dialogue(), Leena)

    assert any(record.levelname == "DEBUG" for record in caplog.records)


def test_a_generated_scene_logs_its_beat_count_at_info(caplog):
    with caplog.at_level("DEBUG", logger="campaign.improviser"):
        build(FakeLLM("Leena: Rain.\nchadwick: Still.")).generate_scene(AMBIENT)

    assert any(record.levelname == "INFO" for record in caplog.records)


def test_nothing_is_logged_above_debug_when_call_raises(Leena, caplog):
    # The caller already handles ImproviserError; logging it too double-reports
    # every failure into the operator's stream at WARNING.
    with caplog.at_level("DEBUG", logger="campaign.improviser"):
        with pytest.raises(ImproviserError):
            build(ExplodingLLM())(dialogue(), Leena)

    assert not [r for r in caplog.records if r.levelno >= 30]
