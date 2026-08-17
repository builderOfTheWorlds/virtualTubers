"""Tests for app/campaign/primitives.py — the action vocabulary of a campaign.

A primitive is the *cosmetic* verb a cast member performs in an action beat:
roll a check, cast a spell, run an exploit. Nothing here simulates anything —
there is no dice engine and no RNG. The script decides what happens; a primitive
only decides how it is narrated. That is what makes a show reproducible: replay
the same pack and you get the same words.

The registry is also the seam that makes a second campaign config rather than
code. Fantasy verbs and cyberpunk verbs live side by side, and a pack enables
the subset it wants.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from campaign.primitives import (  # noqa: E402
    DEFAULT_REGISTRY, ParamSpec, Primitive, PrimitiveError, Registry,
    get, names, render,
)


# ── helpers ──────────────────────────────────────────────────────────────────
def simple_primitive(name="wave", genre="fantasy"):
    return Primitive(
        name=name,
        genre=genre,
        summary="Wave at someone.",
        params=(ParamSpec("target"), ParamSpec("mood", required=False)),
        template="{actor} waves at {target}",
        suffixes=(("mood", ", looking {mood}"),),
    )


# ── registry ─────────────────────────────────────────────────────────────────
def test_registry_starts_empty():
    assert Registry().names() == []


def test_register_then_get_returns_the_same_primitive():
    registry = Registry()
    primitive = simple_primitive()
    registry.register(primitive)

    assert registry.get("wave") is primitive


def test_registering_a_duplicate_name_is_an_error():
    registry = Registry()
    registry.register(simple_primitive())

    with pytest.raises(PrimitiveError, match="wave"):
        registry.register(simple_primitive())


def test_getting_an_unknown_primitive_names_it():
    with pytest.raises(PrimitiveError, match="nonsense"):
        Registry().get("nonsense")


def test_names_are_sorted_for_stable_output():
    registry = Registry()
    for name in ("zap", "attack", "move"):
        registry.register(simple_primitive(name=name))

    assert registry.names() == ["attack", "move", "zap"]


def test_names_can_be_filtered_by_genre():
    registry = Registry()
    registry.register(simple_primitive(name="cast_spell", genre="fantasy"))
    registry.register(simple_primitive(name="scan", genre="cyber"))

    assert registry.names(genre="cyber") == ["scan"]
    assert registry.names(genre="fantasy") == ["cast_spell"]


def test_contains_reports_membership():
    registry = Registry()
    registry.register(simple_primitive())

    assert "wave" in registry
    assert "nonsense" not in registry


# ── param validation ─────────────────────────────────────────────────────────
def test_missing_required_param_names_the_param():
    primitive = simple_primitive()

    with pytest.raises(PrimitiveError, match="target"):
        primitive.validate({})


def test_unknown_param_names_the_param():
    primitive = simple_primitive()

    with pytest.raises(PrimitiveError, match="colour"):
        primitive.validate({"target": "Helen", "colour": "green"})


def test_every_unknown_param_is_reported_in_a_stable_order():
    primitive = simple_primitive()

    with pytest.raises(PrimitiveError) as excinfo:
        primitive.validate({"target": "Helen", "colour": "green", "altitude": 3})

    message = str(excinfo.value)
    assert "colour" in message and "altitude" in message
    assert message.index("altitude") < message.index("colour")


def test_validate_returns_resolved_params_with_defaults_filled():
    primitive = Primitive(
        name="wave", genre="fantasy", summary="",
        params=(ParamSpec("target"), ParamSpec("mood", required=False, default="calm")),
        template="{actor} waves at {target}",
    )

    assert primitive.validate({"target": "Helen"}) == {"target": "Helen", "mood": "calm"}


def test_validate_does_not_mutate_the_caller_dict():
    primitive = Primitive(
        name="wave", genre="fantasy", summary="",
        params=(ParamSpec("target"), ParamSpec("mood", required=False, default="calm")),
        template="{actor} waves at {target}",
    )
    supplied = {"target": "Helen"}
    primitive.validate(supplied)

    assert supplied == {"target": "Helen"}


def test_value_outside_choices_names_the_value():
    primitive = Primitive(
        name="roll", genre="fantasy", summary="",
        params=(ParamSpec("outcome", choices=("success", "failure")),),
        template="{actor} rolls",
    )

    with pytest.raises(PrimitiveError, match="sideways"):
        primitive.validate({"outcome": "sideways"})


def test_value_inside_choices_is_accepted():
    primitive = Primitive(
        name="roll", genre="fantasy", summary="",
        params=(ParamSpec("outcome", choices=("success", "failure")),),
        template="{actor} rolls",
    )

    assert primitive.validate({"outcome": "success"}) == {"outcome": "success"}


def test_absent_optional_param_with_choices_is_not_checked():
    primitive = Primitive(
        name="roll", genre="fantasy", summary="",
        params=(ParamSpec("outcome", required=False, choices=("success", "failure")),),
        template="{actor} rolls",
    )

    assert primitive.validate({}) == {"outcome": None}


# ── rendering ────────────────────────────────────────────────────────────────
def test_render_fills_actor_and_params():
    result = simple_primitive().render("Buffalo", {"target": "Helen"})

    assert "Buffalo" in result and "Helen" in result


def test_render_ends_in_a_sentence():
    assert simple_primitive().render("Buffalo", {"target": "Helen"}).endswith(".")


def test_render_appends_a_suffix_when_its_param_is_supplied():
    result = simple_primitive().render("Buffalo", {"target": "Helen", "mood": "smug"})

    assert "smug" in result


def test_render_omits_a_suffix_when_its_param_is_absent():
    result = simple_primitive().render("Buffalo", {"target": "Helen"})

    assert "looking" not in result


def test_render_validates_its_params():
    with pytest.raises(PrimitiveError, match="target"):
        simple_primitive().render("Buffalo", {})


def test_render_is_deterministic():
    """No dice, no RNG — a pack replays word for word."""
    primitive = simple_primitive()
    first = primitive.render("Buffalo", {"target": "Helen", "mood": "smug"})

    assert all(primitive.render("Buffalo", {"target": "Helen", "mood": "smug"}) == first
               for _ in range(5))


def test_render_accepts_no_params_when_none_are_required():
    primitive = Primitive(name="wait", genre="fantasy", summary="",
                          params=(), template="{actor} waits")

    assert primitive.render("Carl", {}) == "Carl waits."


def test_render_defaults_params_to_empty():
    primitive = Primitive(name="wait", genre="fantasy", summary="",
                          params=(), template="{actor} waits")

    assert primitive.render("Carl") == "Carl waits."


# ── module-level convenience API ─────────────────────────────────────────────
def test_module_get_reads_the_default_registry():
    assert get("roll_check") is DEFAULT_REGISTRY.get("roll_check")


def test_module_names_reads_the_default_registry():
    assert names() == DEFAULT_REGISTRY.names()


def test_module_render_dispatches_by_name():
    result = render("roll_check", "Buffalo", {"skill": "Strength"})

    assert "Buffalo" in result and "Strength" in result


def test_module_render_on_unknown_primitive_names_it():
    with pytest.raises(PrimitiveError, match="telekinesis"):
        render("telekinesis", "Buffalo", {})


# ── the shipped vocabulary ───────────────────────────────────────────────────
FANTASY = ["attack", "cast_spell", "move_to", "reveal_memory", "roll_check", "search"]
CYBER = ["execute_exploit", "scan_target"]


@pytest.mark.parametrize("name", FANTASY)
def test_fantasy_primitive_is_registered(name):
    assert get(name).genre == "fantasy"


@pytest.mark.parametrize("name", CYBER)
def test_cyberpunk_primitive_is_registered(name):
    assert get(name).genre == "cyber"


def test_fantasy_and_cyber_are_the_only_genres():
    assert sorted({get(name).genre for name in names()}) == ["cyber", "fantasy"]


def test_roll_check_does_not_invent_an_outcome():
    """The script owns outcomes; the primitive only narrates."""
    result = render("roll_check", "Buffalo", {"skill": "Perception", "dc": 15})

    assert "15" in result
    assert "success" not in result.lower() and "fail" not in result.lower()


def test_roll_check_can_narrate_a_scripted_outcome():
    result = render("roll_check", "Buffalo",
                    {"skill": "Perception", "outcome": "failure"})

    assert "Perception" in result


def test_roll_check_rejects_an_unscripted_outcome():
    with pytest.raises(PrimitiveError, match="maybe"):
        render("roll_check", "Buffalo", {"skill": "Perception", "outcome": "maybe"})


def test_cast_spell_names_the_spell_and_target():
    result = render("cast_spell", "Helen", {"spell": "Moonbeam", "target": "the wraith"})

    assert "Moonbeam" in result and "the wraith" in result


def test_reveal_memory_exists_for_the_sage():
    """The loop-carrying character projects a memory of a previous run."""
    result = render("reveal_memory", "Drokki", {"subject": "the moonwell"})

    assert "the moonwell" in result


@pytest.mark.parametrize("name", FANTASY + CYBER)
def test_every_shipped_primitive_renders_from_required_params_alone(name):
    primitive = get(name)
    params = {spec.name: f"<{spec.name}>" for spec in primitive.params if spec.required}
    for spec in primitive.params:
        if spec.required and spec.choices:
            params[spec.name] = spec.choices[0]

    result = primitive.render("Buffalo", params)

    assert result.startswith("Buffalo") and result.endswith(".")


@pytest.mark.parametrize("name", FANTASY + CYBER)
def test_every_shipped_primitive_has_a_summary(name):
    assert get(name).summary


@pytest.mark.parametrize("name", FANTASY + CYBER)
def test_template_only_references_declared_params(name):
    """A typo in a template would otherwise surface as a KeyError mid-show."""
    import string

    primitive = get(name)
    declared = {spec.name for spec in primitive.params} | {"actor"}

    text = primitive.template + "".join(fragment for _, fragment in primitive.suffixes)
    referenced = {field for _, field, _, _ in string.Formatter().parse(text) if field}

    assert referenced <= declared


@pytest.mark.parametrize("name", FANTASY + CYBER)
def test_suffixes_only_key_off_declared_params(name):
    primitive = get(name)
    declared = {spec.name for spec in primitive.params}

    assert {key for key, _ in primitive.suffixes} <= declared
