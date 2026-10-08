"""P3.3 frozen tests for app/table/commit_check.py (build plan P3.3; agent_dnd §5.3).

Commit validation is a cheap, local, deterministic gate the arbiter runs
BEFORE a reply is committed (no LLM). A failure becomes a `retake` with the
reason string. One failing-input test per rule, plus the passing cases that
must not be over-caught.

Rules (checked in this order; the first failure wins):
  silent_seat       a silent seat (system prompt says "you never speak") produced a line
  empty             nothing left after stripping whitespace/quotes
  meta              out-of-character / model-meta text (AI disclaimers, <think> tags, OOC)
  name_label        the line starts with the seat's OWN name label ("Chadwick: ...")
  speaks_for_other  the line voices or narrates ANOTHER cast member
  stage_direction   *asterisk actions* or (parenthetical asides): spoken lines only
                    (agent_dnd §5.3; seen in the W0 MoE run: "*Stomps*", "(Why do I know that name?)")
  narration         quoted speech wrapped in prose ("I lean in. \"...\" I whisper"): the
                    reply narrates instead of only speaking (seen in the W0 Qwen3.8-27B-FP8 run)
  too_long          more than max_words words or max_lines lines
  forbidden_leak    contains a forbidden phrase (case/whitespace-insensitive)
"""
import pytest

from pending import require

cc = require("table.commit_check", "app/table/commit_check.py", wp="P3.3")

CAST = {"tuber_1": "Chadwick", "tuber_2": "Leena", "tuber_3": "Sodacan Bob", "tuber_4": "Vigil"}


def ctx(**over):
    base = dict(seat="tuber_1", cast_names=CAST, silent_seats=frozenset(),
                forbidden_phrases=("the true purpose of Malmont",), max_words=60, max_lines=2)
    base.update(over)
    return cc.CommitContext(**base)


def check(text, **over):
    return cc.check_reply(text, ctx(**over))


# ── passing cases (must not be over-caught) ────────────────────────────────

@pytest.mark.parametrize("text", [
    "Oh no. Oh no no no. Leena, tell me that lock isn't humming.",
    "I'll hold the torch, Vigil, but I'm not going in first.",
    "Bob, if that's a trap, I'm writing a very sad ballad about you.",
    "Malmont? I've heard the name in a tavern song, nothing more.",
])
def test_ordinary_in_character_lines_pass(text):
    result = check(text)
    assert result.ok, result
    assert result.code is None and result.reason == ""


def test_addressing_another_character_by_name_is_fine():
    # Naming someone is not speaking for them.
    assert check("Leena: can you open it?").ok is False   # (label form is caught below)
    assert check("Can you open it, Leena?").ok


# ── one failing input per rule ─────────────────────────────────────────────

def test_silent_seat_any_line_fails():
    r = check("Hello everyone.", seat="tuber_4", silent_seats=frozenset({"tuber_4"}))
    assert (r.ok, r.code) == (False, "silent_seat")


def test_silent_seat_empty_line_is_the_correct_output():
    r = check("", seat="tuber_4", silent_seats=frozenset({"tuber_4"}))
    assert r.ok


@pytest.mark.parametrize("text", ["", "   ", '""', "\n\n", "''"])
def test_empty_fails(text):
    assert check(text).code == "empty"


@pytest.mark.parametrize("text", [
    "As an AI language model, I can't pretend to be Chadwick.",
    "<think>I should be nervous</think> Oh no.",
    "</think>Oh no.",
    "(OOC: is this the right scene?)",
    "OOC: brb",
    "[GM note: skip this]",
])
def test_meta_fails(text):
    assert check(text).code == "meta"


@pytest.mark.parametrize("text", ["Chadwick: Oh no.", "CHADWICK - Oh no.", "**Chadwick:** Oh no."])
def test_own_name_label_fails(text):
    assert check(text).code == "name_label"


@pytest.mark.parametrize("text", [
    "Leena: I can open it.",                                  # writes another's line
    "Oh no.\nVigil: Stand back.",                             # second line voices Vigil
    'Leena says, "I can open it."',                           # narrates another's speech
    "Sodacan Bob nods and draws his sword.",                  # narrates another's action
    "Vigil replied that we should wait.",
])
def test_speaks_for_other_fails(text):
    r = check(text)
    assert r.code == "speaks_for_other", r
    assert any(name in r.reason for name in CAST.values())


@pytest.mark.parametrize("text", [
    "*adjusts lute nervously* I've got a song for this!",
    "This sigil... it's the same as the one in my village. (Why do I know that name?)",
    "Hmph. *Stomps near the fracture.* Crude work.",
    "(whispering) Stay back.",
])
def test_stage_direction_fails(text):
    assert check(text).code == "stage_direction"


@pytest.mark.parametrize("text", [
    "Two exits, one barred. Smiles don't open doors.",
    "It's 3 * 4 paces to the wall.",          # a lone asterisk is not an action
    "Stay back:) I mean it.",                  # emoticon, not a parenthetical
])
def test_stage_direction_not_over_caught(text):
    assert check(text).ok, check(text)


@pytest.mark.parametrize("text", [
    'I lean in, letting the silence press. "That should not feel familiar," I whisper.',
    '"Clean break, but wrong metal." I kneel and press my thumb to the fracture.',
    '“It’s dead,” I mutter.',
])
def test_narration_fails(text):
    r = check(text)
    assert r.code == "narration", r


@pytest.mark.parametrize("text", [
    '"Clean break, but wrong metal."',                       # only speech, quoted
    "Clean break. Not accident. Someone knew what they were doing.",
    'They call it the "moonwell", and it is dry.',           # quoting a word inside speech
    '"Run!"',
])
def test_narration_not_over_caught(text):
    assert check(text).ok, check(text)


def test_too_long_by_words():
    assert check(" ".join(["la"] * 61)).code == "too_long"
    assert check(" ".join(["la"] * 60)).ok


def test_too_long_by_lines():
    assert check("One.\nTwo.\nThree.").code == "too_long"
    assert check("One.\nTwo.").ok


@pytest.mark.parametrize("text", [
    "I know the true purpose of Malmont, friends.",
    "I know THE TRUE   PURPOSE of\nMalmont.",
])
def test_forbidden_leak_fails_case_and_whitespace_insensitive(text):
    r = check(text)
    assert r.code == "forbidden_leak"
    assert "the true purpose of Malmont" in r.reason


def test_first_failure_wins_in_rule_order():
    # meta AND too long AND leak -> meta is reported
    text = "As an AI, " + " ".join(["la"] * 70) + " the true purpose of Malmont"
    assert check(text).code == "meta"


def test_reason_is_retake_ready():
    r = check("Leena: I can open it.")
    assert r.reason.startswith("speaks_for_other:")


def test_rule_codes_constant():
    # extended 2026-10-07 by the orchestrator (test author): + narration, + repeats
    assert cc.RULES == ("silent_seat", "empty", "meta", "name_label", "speaks_for_other",
                        "stage_direction", "narration", "too_long", "repeats", "forbidden_leak")


# ── silent-seat derivation reuses the improviser rule ──────────────────────

def test_is_silent_prompt_matches_improviser_rule():
    assert cc.is_silent_prompt("You are the Party Member. You never speak.")
    assert cc.is_silent_prompt("YOU NEVER SPEAK on stream.")
    assert not cc.is_silent_prompt("You speak rarely.")
    assert not cc.is_silent_prompt(None)


def test_silent_seats_from_seat_prompts():
    prompts = {"tuber_1": "You are Chadwick.", "tuber_7": "Observer. You never speak."}
    assert cc.silent_seats(prompts) == frozenset({"tuber_7"})


def test_context_is_immutable():
    c = ctx()
    with pytest.raises(Exception):
        c.seat = "tuber_2"



# ── repeats (added 2026-10-08: the W0 MoE slice collapsed into verbatim repetition) ──

PRIOR = ("This is the way. Ask them plainly, or I will.",
         "The heavy oak door slams shut behind you. Torchlight glares off marble floors.")


@pytest.mark.parametrize("text", [
    "This is the way. Ask them plainly, or I will.",            # verbatim
    "this is the way -- ask them plainly or i will!",            # same words, other punctuation
    "This is the way. Ask them plainly, or I will. Now.",        # near-duplicate
    "The heavy oak door slams shut behind you. Torchlight glares off marble floors. Move.",
])
def test_repeats_fails(text):
    r = cc.check_reply(text, ctx(prior_lines=PRIOR))
    assert r.code == "repeats", r


@pytest.mark.parametrize("text", [
    "This is the way. Burn them first.",                         # the tic is fine, the line is new
    "Ask him who sent the card.",
    "Plainly.",                                                  # too short to judge
])
def test_repeats_not_over_caught(text):
    assert cc.check_reply(text, ctx(prior_lines=PRIOR)).ok


def test_repeats_needs_prior_lines():
    assert cc.check_reply(PRIOR[0], ctx()).ok
