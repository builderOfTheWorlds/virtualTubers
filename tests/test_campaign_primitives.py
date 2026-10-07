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
        primitive.validate({"target": "Leena", "colour": "green"})


def test_every_unknown_param_is_reported_in_a_stable_order():
    primitive = simple_primitive()

    with pytest.raises(PrimitiveError) as excinfo:
        primitive.validate({"target": "Leena", "colour": "green", "altitude": 3})

    message = str(excinfo.value)
    assert "colour" in message and "altitude" in message
    assert message.index("altitude") < message.index("colour")


def test_validate_returns_resolved_params_with_defaults_filled():
    primitive = Primitive(
        name="wave", genre="fantasy", summary="",
        params=(ParamSpec("target"), ParamSpec("mood", required=False, default="calm")),
        template="{actor} waves at {target}",
    )

    assert primitive.validate({"target": "Leena"}) == {"target": "Leena", "mood": "calm"}


def test_validate_does_not_mutate_the_caller_dict():
    primitive = Primitive(
        name="wave", genre="fantasy", summary="",
        params=(ParamSpec("target"), ParamSpec("mood", required=False, default="calm")),
        template="{actor} waves at {target}",
    )
    supplied = {"target": "Leena"}
    primitive.validate(supplied)

    assert supplied == {"target": "Leena"}


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
    result = simple_primitive().render("Chadwick", {"target": "Leena"})

    assert "Chadwick" in result and "Leena" in result


def test_render_ends_in_a_sentence():
    assert simple_primitive().render("Chadwick", {"target": "Leena"}).endswith(".")


def test_render_appends_a_suffix_when_its_param_is_supplied():
    result = simple_primitive().render("Chadwick", {"target": "Leena", "mood": "smug"})

    assert "smug" in result


def test_render_omits_a_suffix_when_its_param_is_absent():
    result = simple_primitive().render("Chadwick", {"target": "Leena"})

    assert "looking" not in result


def test_render_validates_its_params():
    with pytest.raises(PrimitiveError, match="target"):
        simple_primitive().render("Chadwick", {})


def test_render_is_deterministic():
    """No dice, no RNG — a pack replays word for word."""
    primitive = simple_primitive()
    first = primitive.render("Chadwick", {"target": "Leena", "mood": "smug"})

    assert all(primitive.render("Chadwick", {"target": "Leena", "mood": "smug"}) == first
               for _ in range(5))


def test_render_accepts_no_params_when_none_are_required():
    primitive = Primitive(name="wait", genre="fantasy", summary="",
                          params=(), template="{actor} waits")

    assert primitive.render("Vigil", {}) == "Vigil waits."


def test_render_defaults_params_to_empty():
    primitive = Primitive(name="wait", genre="fantasy", summary="",
                          params=(), template="{actor} waits")

    assert primitive.render("Vigil") == "Vigil waits."


# ── module-level convenience API ─────────────────────────────────────────────
def test_module_get_reads_the_default_registry():
    assert get("roll_check") is DEFAULT_REGISTRY.get("roll_check")


def test_module_names_reads_the_default_registry():
    assert names() == DEFAULT_REGISTRY.names()


def test_module_render_dispatches_by_name():
    result = render("roll_check", "Chadwick", {"skill": "Strength"})

    assert "Chadwick" in result and "Strength" in result


def test_module_render_on_unknown_primitive_names_it():
    with pytest.raises(PrimitiveError, match="telekinesis"):
        render("telekinesis", "Chadwick", {})


# ── the shipped vocabulary ───────────────────────────────────────────────────
FANTASY = ["attack", "cast_spell", "move_to", "reveal_memory", "roll_check", "search"]
CYBER = ["execute_exploit", "scan_target"]


@pytest.mark.parametrize("name", FANTASY)
def test_fantasy_primitive_is_registered(name):
    assert get(name).genre == "fantasy"


@pytest.mark.parametrize("name", CYBER)
def test_cyberpunk_primitive_is_registered(name):
    assert get(name).genre == "cyber"


def test_fantasy_cyber_and_office_are_the_only_genres():
    assert sorted({get(name).genre for name in names()}) == [
        "cyber", "cyber_police", "fantasy", "office"]


CYBER_POLICE = ["assign_lead", "brief_press", "file_report", "interrogate",
                "open_case", "raid", "request_backup", "requisition",
                "seize_evidence", "stand_watch", "trace_signal"]


def test_cyber_police_genre_lists_exactly_the_cyber_police_verbs():
    assert names(genre="cyber_police") == CYBER_POLICE


@pytest.mark.parametrize("name", CYBER_POLICE)
def test_cyber_police_primitive_is_registered(name):
    assert get(name).genre == "cyber_police"


def test_roll_check_does_not_invent_an_outcome():
    """The script owns outcomes; the primitive only narrates."""
    result = render("roll_check", "Chadwick", {"skill": "Perception", "dc": 15})

    assert "15" in result
    assert "success" not in result.lower() and "fail" not in result.lower()


def test_roll_check_can_narrate_a_scripted_outcome():
    result = render("roll_check", "Chadwick",
                    {"skill": "Perception", "outcome": "failure"})

    assert "Perception" in result


def test_roll_check_rejects_an_unscripted_outcome():
    with pytest.raises(PrimitiveError, match="maybe"):
        render("roll_check", "Chadwick", {"skill": "Perception", "outcome": "maybe"})


def test_cast_spell_names_the_spell_and_target():
    result = render("cast_spell", "Leena", {"spell": "Moonbeam", "target": "the wraith"})

    assert "Moonbeam" in result and "the wraith" in result


def test_reveal_memory_exists_for_the_sage():
    """The loop-carrying character projects a memory of a previous run."""
    result = render("reveal_memory", "Sodacan Bob", {"subject": "the moonwell"})

    assert "the moonwell" in result


@pytest.mark.parametrize("name", FANTASY + CYBER)
def test_every_shipped_primitive_renders_from_required_params_alone(name):
    primitive = get(name)
    params = {spec.name: f"<{spec.name}>" for spec in primitive.params if spec.required}
    for spec in primitive.params:
        if spec.required and spec.choices:
            params[spec.name] = spec.choices[0]

    result = primitive.render("Chadwick", params)

    assert result.startswith("Chadwick") and result.endswith(".")


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


# ── office vocabulary (ashiorid_office) ──────────────────────────────────────
OFFICE = ["assign_task", "brew_coffee", "commit", "deploy", "file_bug", "hr_notice",
          "merge_pr", "observe", "open_pr", "open_ticket", "pitch", "run_tests",
          "take_out_trash", "write_spec"]


def test_office_genre_lists_exactly_the_office_verbs():
    assert names(genre="office") == OFFICE


def test_office_verbs_do_not_disturb_the_existing_genres():
    assert names(genre="fantasy") == FANTASY
    assert names(genre="cyber") == CYBER


def _required_only(primitive):
    params = {spec.name: f"<{spec.name}>" for spec in primitive.params if spec.required}
    for spec in primitive.params:
        if spec.required and spec.choices:
            params[spec.name] = spec.choices[0]
    return params


def _every_param(primitive):
    return {spec.name: (spec.choices[0] if spec.choices else f"<{spec.name}>")
            for spec in primitive.params}


@pytest.mark.parametrize("name", OFFICE)
def test_office_primitive_renders_from_required_params_alone(name):
    primitive = get(name)
    result = primitive.render("Mara", _required_only(primitive))

    assert result.startswith("Mara") and result.endswith(".")


@pytest.mark.parametrize("name", OFFICE)
def test_office_primitive_renders_with_every_param(name):
    primitive = get(name)
    result = primitive.render("Mara", _every_param(primitive))

    assert result.startswith("Mara") and result.endswith(".")
    for value in _every_param(primitive).values():
        assert str(value) in result


@pytest.mark.parametrize("name", OFFICE)
def test_office_primitive_is_byte_identical_across_renders(name):
    primitive = get(name)
    params = _every_param(primitive)

    assert len({primitive.render("Mara", params) for _ in range(5)}) == 1


@pytest.mark.parametrize("name", OFFICE)
def test_office_primitive_has_a_summary(name):
    assert get(name).summary


@pytest.mark.parametrize("name", OFFICE)
def test_office_template_only_references_declared_params(name):
    import string

    primitive = get(name)
    declared = {spec.name for spec in primitive.params} | {"actor"}
    text = primitive.template + "".join(fragment for _, fragment in primitive.suffixes)
    referenced = {field for _, field, _, _ in string.Formatter().parse(text) if field}

    assert referenced <= declared
    assert {key for key, _ in primitive.suffixes} <= {spec.name for spec in primitive.params}


@pytest.mark.parametrize("name", OFFICE)
def test_office_primitive_rejects_an_unknown_param(name):
    with pytest.raises(PrimitiveError, match="bogus"):
        get(name).render("Mara", {**_required_only(get(name)), "bogus": 1})


@pytest.mark.parametrize("name,missing", [
    ("assign_task", "to"), ("assign_task", "task"), ("write_spec", "topic"),
    ("open_ticket", "title"), ("commit", "message"), ("run_tests", "suite"),
    ("file_bug", "title"), ("open_pr", "title"), ("merge_pr", "pr"),
    ("deploy", "environment"), ("pitch", "idea"), ("hr_notice", "subject"),
])
def test_office_primitive_missing_required_param_is_named(name, missing):
    params = _required_only(get(name))
    del params[missing]

    with pytest.raises(PrimitiveError, match=missing):
        render(name, "Mara", params)


def test_assign_task_names_assignee_and_task():
    assert render("assign_task", "Mara", {"to": "Dev", "task": "the rate limiter",
                                          "due": "by standup"}) == \
        "Mara assigns the rate limiter to Dev, due by standup."


def test_run_tests_does_not_invent_a_result():
    """The script owns results; run_tests never decides pass or fail."""
    result = render("run_tests", "Quinn", {"suite": "the scoring suite"})

    assert result == "Quinn runs the scoring suite."
    assert "pass" not in result and "fail" not in result


@pytest.mark.parametrize("outcome", ["pass", "fail"])
def test_run_tests_narrates_a_scripted_result(outcome):
    assert outcome in render("run_tests", "Quinn", {"suite": "unit", "result": outcome})


def test_run_tests_rejects_an_unscripted_result():
    with pytest.raises(PrimitiveError, match="flaky"):
        render("run_tests", "Quinn", {"suite": "unit", "result": "flaky"})


def test_deploy_rejects_an_unknown_outcome():
    with pytest.raises(PrimitiveError, match="exploded"):
        render("deploy", "Dev", {"environment": "staging", "outcome": "exploded"})


def test_file_bug_rejects_an_unknown_severity():
    with pytest.raises(PrimitiveError, match="apocalyptic"):
        render("file_bug", "Quinn", {"title": "x", "severity": "apocalyptic"})


def test_observe_needs_no_params_for_the_silent_watcher():
    assert render("observe", "The Party Member") == "The Party Member watches."


def test_observe_can_name_what_is_watched():
    assert render("observe", "The Party Member", {"target": "the standup"}) == \
        "The Party Member watches the standup."


def test_take_out_trash_and_brew_coffee_need_no_params():
    assert render("take_out_trash", "Pat") == "Pat takes out the trash."
    assert render("brew_coffee", "Pat") == "Pat brews a fresh pot of coffee."


def test_office_render_uses_only_the_primitive_error_type():
    """Bad input surfaces as PrimitiveError, never KeyError or TypeError."""
    for name in OFFICE:
        with pytest.raises(PrimitiveError):
            render(name, "Mara", {"definitely_not_a_param": "x"})
