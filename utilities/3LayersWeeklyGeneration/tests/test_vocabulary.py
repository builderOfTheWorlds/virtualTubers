"""Acceptance tests for src/vocabulary.py — the one closed-set validator.

Issue #3 and design decision D13 are the same piece of work: anything a layer
invents that is not in a declared vocabulary must FAIL LOUDLY rather than
silently evaporate. A `lore:` stem the pack has never heard of is dropped by
`LLMImproviser` without a word; a `depends_on` key outside the state
vocabulary would produce a take conditioned on something no runtime can ever
satisfy. Both are the same bug, so both are caught here, once.
"""
import pytest

import vocabulary as vocabulary_module


CONFIG = {
    "state": {
        "flags": ["helen-wounded", "moonwell-tainted", "buffalo-lost-axe"],
        "moods": ["tense", "weary", "hopeful", "giddy"],
        "carry_keys": ["helen-wounded", "moonwell-tainted"],
    }
}


class FakePack:
    """The two attributes the validator reads off a CampaignPack."""

    def __init__(self, lore=None, scenes=None):
        self.lore = lore if lore is not None else {
            "the-moonwell": "text", "leto-manor": "text", "the-loop": "text",
        }
        self.scenes = scenes if scenes is not None else {
            "01-invitation": object(), "10-portal-encounter": object(),
            "a01-camp-fire": object(),
        }


@pytest.fixture
def vocab():
    return vocabulary_module.Vocabulary.from_config_and_pack(CONFIG, FakePack())


# ── construction ──────────────────────────────────────────────────────────────

def test_from_config_and_pack_exposes_each_closed_set(vocab):
    assert vocab.flags == frozenset(CONFIG["state"]["flags"])
    assert vocab.moods == frozenset(CONFIG["state"]["moods"])
    assert vocab.carry_keys == frozenset(CONFIG["state"]["carry_keys"])
    assert vocab.lore_stems == frozenset({"the-moonwell", "leto-manor", "the-loop"})
    assert vocab.scene_ids == frozenset(
        {"01-invitation", "10-portal-encounter", "a01-camp-fire"})


def test_carry_keys_must_be_a_subset_of_flags():
    """`carry_keys` is documented as a subset of flags; enforce it at load."""
    broken = {"state": {"flags": ["a"], "moods": ["tense"], "carry_keys": ["a", "b"]}}
    with pytest.raises(vocabulary_module.VocabularyError) as excinfo:
        vocabulary_module.Vocabulary.from_config_and_pack(broken, FakePack())
    assert "b" in str(excinfo.value)


def test_missing_state_block_raises_vocabulary_error():
    with pytest.raises(vocabulary_module.VocabularyError):
        vocabulary_module.Vocabulary.from_config_and_pack({}, FakePack())


# ── lore stems (issue #3) ─────────────────────────────────────────────────────

def test_unknown_lore_returns_only_the_offending_stems(vocab):
    assert vocab.unknown_lore(["the-moonwell", "invented-stem"]) == ["invented-stem"]


def test_unknown_lore_is_empty_for_a_fully_valid_set(vocab):
    assert vocab.unknown_lore(["the-moonwell", "the-loop"]) == []


def test_unknown_lore_accepts_an_empty_or_none_input(vocab):
    assert vocab.unknown_lore([]) == []
    assert vocab.unknown_lore(None) == []


def test_unknown_lore_preserves_input_order_and_does_not_deduplicate_away_signal(vocab):
    assert vocab.unknown_lore(["zeta", "the-loop", "alpha"]) == ["zeta", "alpha"]


# ── state keys (D13) ──────────────────────────────────────────────────────────

def test_unknown_state_keys_flags_anything_outside_the_vocabulary(vocab):
    assert vocab.unknown_state_keys(["helen-wounded", "dragon-appeased"]) == \
        ["dragon-appeased"]


def test_mood_is_a_valid_state_key(vocab):
    """`mood` is the coarse tone dial, addressed by that literal key name."""
    assert vocab.unknown_state_keys(["mood"]) == []


def test_unknown_carry_keys_is_narrower_than_state_keys(vocab):
    """A flag that does not survive a loop reset is not a valid carry key."""
    assert vocab.unknown_state_keys(["buffalo-lost-axe"]) == []
    assert vocab.unknown_carry_keys(["buffalo-lost-axe"]) == ["buffalo-lost-axe"]
    assert vocab.unknown_carry_keys(["helen-wounded"]) == []


# ── conditions: a take's `conditions:` mapping ────────────────────────────────

def test_validate_condition_accepts_a_boolean_flag(vocab):
    assert vocab.validate_condition({"helen-wounded": True}) == []
    assert vocab.validate_condition({"moonwell-tainted": False}) == []


def test_validate_condition_accepts_a_declared_mood(vocab):
    assert vocab.validate_condition({"mood": "tense"}) == []


def test_validate_condition_rejects_an_undeclared_mood_value(vocab):
    problems = vocab.validate_condition({"mood": "peckish"})
    assert problems and any("peckish" in p for p in problems)


def test_validate_condition_rejects_a_non_boolean_flag_value(vocab):
    """Flags are booleans. A string value means the model invented a schema."""
    problems = vocab.validate_condition({"helen-wounded": "very"})
    assert problems and any("helen-wounded" in p for p in problems)


def test_validate_condition_rejects_an_unknown_key(vocab):
    problems = vocab.validate_condition({"dragon-appeased": True})
    assert problems and any("dragon-appeased" in p for p in problems)


def test_validate_condition_accepts_the_empty_neutral_condition(vocab):
    """`conditions: {}` is the neutral take — always valid, never a problem."""
    assert vocab.validate_condition({}) == []


def test_validate_condition_reports_every_problem_not_just_the_first(vocab):
    problems = vocab.validate_condition({"nope-one": True, "mood": "peckish"})
    assert len(problems) == 2


# ── scene refs ────────────────────────────────────────────────────────────────

def test_unknown_scene_refs_catches_a_spine_slot_pointing_at_nothing(vocab):
    assert vocab.unknown_scene_refs(["01-invitation", "99-nonexistent"]) == \
        ["99-nonexistent"]


# ── check(): turning problems into a raised, diagnosable failure ──────────────

def test_check_is_a_no_op_when_there_are_no_problems():
    vocabulary_module.check([], "seg-001 slot-004")


def test_check_raises_naming_the_location_and_every_offender():
    with pytest.raises(vocabulary_module.VocabularyError) as excinfo:
        vocabulary_module.check(["unknown lore stem 'zeta'", "unknown key 'nope'"],
                                "seg-001 slot-004")
    message = str(excinfo.value)
    assert "seg-001 slot-004" in message
    assert "zeta" in message
    assert "nope" in message


def test_check_logs_at_error_before_raising(caplog):
    caplog.set_level("ERROR")
    with pytest.raises(vocabulary_module.VocabularyError):
        vocabulary_module.check(["unknown key 'nope'"], "seg-002")
    assert caplog.records
    assert all(record.levelname == "ERROR" for record in caplog.records)


# ── the whole-slot convenience pass every caller actually uses ────────────────

def test_validate_slot_accepts_a_well_formed_ambient_slot(vocab):
    slot = {
        "slot_id": "s-001", "kind": "ambient", "prompt": "A quiet moment.",
        "lore": ["the-moonwell"], "participants": ["helen"],
        "sensitivity": "flags", "depends_on": ["helen-wounded"],
    }
    assert vocab.validate_slot(slot) == []


def test_validate_slot_collects_lore_and_state_problems_together(vocab):
    slot = {
        "slot_id": "s-002", "kind": "ambient", "prompt": "x",
        "lore": ["invented-stem"], "sensitivity": "flags",
        "depends_on": ["dragon-appeased"],
    }
    problems = vocab.validate_slot(slot)
    assert any("invented-stem" in p for p in problems)
    assert any("dragon-appeased" in p for p in problems)


def test_validate_slot_rejects_an_unknown_sensitivity_value(vocab):
    slot = {"slot_id": "s-003", "kind": "ambient", "prompt": "x",
            "sensitivity": "vibes", "depends_on": []}
    assert any("vibes" in p for p in vocab.validate_slot(slot))


def test_validate_slot_requires_depends_on_to_be_empty_unless_flags_sensitive(vocab):
    """`depends_on` is only meaningful for `sensitivity: flags` (D14)."""
    slot = {"slot_id": "s-004", "kind": "ambient", "prompt": "x",
            "sensitivity": "none", "depends_on": ["helen-wounded"]}
    assert vocab.validate_slot(slot)


def test_validate_slot_checks_scene_ref_for_a_spine_slot(vocab):
    slot = {"slot_id": "s-005", "kind": "spine", "scene_ref": "99-nonexistent",
            "participants": [], "summary": "x"}
    assert any("99-nonexistent" in p for p in vocab.validate_slot(slot))


def test_validate_slot_rejects_an_unknown_kind(vocab):
    assert any("interlude" in p
               for p in vocab.validate_slot({"slot_id": "s-006", "kind": "interlude"}))


@pytest.mark.parametrize("bad_config", [
    {},
    {"state": {"flags": ["a"], "moods": ["tense"], "carry_keys": ["a", "b"]}},
])
def test_construction_failures_log_at_error_before_raising(caplog, bad_config):
    """A config that cannot build a vocabulary must say so in the log, not only
    in a traceback the unattended run never shows anyone."""
    caplog.set_level("ERROR")
    with pytest.raises(vocabulary_module.VocabularyError):
        vocabulary_module.Vocabulary.from_config_and_pack(bad_config, FakePack())
    assert caplog.records
    assert all(record.levelname == "ERROR" for record in caplog.records)
