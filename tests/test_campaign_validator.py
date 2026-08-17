"""Tests for app/campaign/validator.py — semantic validation of a loaded pack.

campaign.pack guarantees a pack is structurally well-formed. This module
answers the next question: does it actually hang together? Branch targets that
point at nothing, beats spoken by cast members who don't exist, action beats
naming primitives the campaign never enabled — all load fine and only explode
mid-show, so they are caught here.

Contract: collect EVERY problem into a report rather than raising on the first
one (an operator fixing a pack wants the whole list), and separate hard errors
from warnings — an unreachable scene is suspicious, not fatal.
"""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from campaign.pack import Beat, Branch, CampaignPack, CastMember, Scene  # noqa: E402
from campaign.validator import (  # noqa: E402
    CampaignInvalid, ValidationReport, validate_pack,
)


# ── helpers ──────────────────────────────────────────────────────────────────
def make_pack(scenes, cast=None, primitives=None, start_scene="opening"):
    """Build a CampaignPack directly — bypasses disk, keeps tests focused."""
    if cast is None:
        cast = {
            "gm": CastMember(id="gm", name="GM", role="gm"),
            "alice": CastMember(id="alice", name="Alice", role="player"),
        }
    return CampaignPack(
        name="testpack",
        title="Test",
        genre="fantasy",
        start_scene=start_scene,
        gm_id="gm",
        player_ids=[m for m in cast if m != "gm"],
        primitives=["roll_check", "cast_spell"] if primitives is None else primitives,
        theme={},
        cast=cast,
        scenes={scene.id: scene for scene in scenes},
        root=Path("/nonexistent"),
        lore_dir=None,
    )


def narration(text="Something happens.", speaker="gm"):
    return Beat(kind="narration", speaker=speaker, text=text)


VALID_SCENES = [
    Scene(id="opening", beats=[narration()],
          branches=[Branch(id="win", next="victory", when={"outcome": "success"})],
          default_next="victory"),
    Scene(id="victory", beats=[narration("You win.")]),
]


# ── report shape ─────────────────────────────────────────────────────────────
def test_valid_pack_produces_clean_report():
    report = validate_pack(make_pack(VALID_SCENES))

    assert isinstance(report, ValidationReport)
    assert report.errors == []
    assert report.ok is True
    assert bool(report) is True


def test_clean_report_raise_if_invalid_is_a_noop():
    validate_pack(make_pack(VALID_SCENES)).raise_if_invalid()


def test_invalid_report_is_falsey_and_raises():
    scenes = [Scene(id="opening", beats=[narration()], default_next="nowhere")]
    report = validate_pack(make_pack(scenes))

    assert report.ok is False
    assert bool(report) is False
    with pytest.raises(CampaignInvalid):
        report.raise_if_invalid()


def test_report_collects_every_error_not_just_the_first():
    scenes = [
        Scene(id="opening",
              beats=[Beat(kind="dialogue", speaker="ghost", text="boo"),
                     Beat(kind="action", speaker="alice", primitive="not_enabled")],
              default_next="nowhere"),
    ]
    report = validate_pack(make_pack(scenes))

    assert len(report.errors) >= 3
    joined = " ".join(report.errors)
    assert "ghost" in joined and "not_enabled" in joined and "nowhere" in joined


def test_raised_error_message_contains_the_problems():
    scenes = [Scene(id="opening", beats=[narration()], default_next="nowhere")]

    with pytest.raises(CampaignInvalid, match="nowhere"):
        validate_pack(make_pack(scenes)).raise_if_invalid()


# ── branch / graph integrity ─────────────────────────────────────────────────
def test_branch_pointing_at_unknown_scene_is_an_error():
    scenes = [
        Scene(id="opening", beats=[narration()],
              branches=[Branch(id="win", next="atlantis")]),
    ]
    report = validate_pack(make_pack(scenes))

    assert any("atlantis" in error for error in report.errors)


def test_default_next_pointing_at_unknown_scene_is_an_error():
    scenes = [Scene(id="opening", beats=[narration()], default_next="atlantis")]
    report = validate_pack(make_pack(scenes))

    assert any("atlantis" in error for error in report.errors)


def test_duplicate_branch_ids_within_a_scene_is_an_error():
    scenes = [
        Scene(id="opening", beats=[narration()],
              branches=[Branch(id="win", next="victory"),
                        Branch(id="win", next="opening")]),
        Scene(id="victory", beats=[narration()]),
    ]
    report = validate_pack(make_pack(scenes))

    assert any("win" in error for error in report.errors)


@pytest.mark.parametrize("weight", ["5", None, [1], "heavy"])
def test_non_numeric_branch_weight_is_an_error(weight):
    """WeightedRandomSelector compares weights numerically — catch it at load."""
    scenes = [
        Scene(id="opening", beats=[narration()],
              branches=[Branch(id="win", next="opening", when={"weight": weight})]),
    ]
    report = validate_pack(make_pack(scenes))

    assert any("weight" in error for error in report.errors)


def test_negative_branch_weight_is_an_error():
    scenes = [
        Scene(id="opening", beats=[narration()],
              branches=[Branch(id="win", next="opening", when={"weight": -2})]),
    ]
    report = validate_pack(make_pack(scenes))

    assert any("-2" in error for error in report.errors)


@pytest.mark.parametrize("weight", [0, 1, 7, 2.5])
def test_valid_branch_weights_are_accepted(weight):
    scenes = [
        Scene(id="opening", beats=[narration()],
              branches=[Branch(id="win", next="opening", when={"weight": weight})]),
    ]

    assert validate_pack(make_pack(scenes)).ok is True


def test_a_boolean_weight_is_an_error():
    """bool is an int subclass, so True would silently pass a naive check."""
    scenes = [
        Scene(id="opening", beats=[narration()],
              branches=[Branch(id="win", next="opening", when={"weight": True})]),
    ]
    report = validate_pack(make_pack(scenes))

    assert any("weight" in error for error in report.errors)


def test_start_scene_missing_from_scenes_is_an_error():
    scenes = [Scene(id="victory", beats=[narration()])]
    report = validate_pack(make_pack(scenes, start_scene="opening"))

    assert any("opening" in error for error in report.errors)


def test_self_referencing_scene_is_allowed():
    """Loops are the premise of the show, not a bug."""
    scenes = [Scene(id="opening", beats=[narration()], default_next="opening")]

    assert validate_pack(make_pack(scenes)).ok is True


# ── beat integrity ───────────────────────────────────────────────────────────
def test_unknown_speaker_is_an_error():
    scenes = [Scene(id="opening", beats=[narration(speaker="ghost")])]
    report = validate_pack(make_pack(scenes))

    assert any("ghost" in error for error in report.errors)


def test_action_beat_naming_an_unenabled_primitive_is_an_error():
    scenes = [
        Scene(id="opening",
              beats=[Beat(kind="action", speaker="alice", primitive="fireball")]),
    ]
    report = validate_pack(make_pack(scenes))

    assert any("fireball" in error for error in report.errors)


def test_action_beat_without_a_primitive_is_an_error():
    scenes = [Scene(id="opening", beats=[Beat(kind="action", speaker="alice")])]
    report = validate_pack(make_pack(scenes))

    assert any("primitive" in error for error in report.errors)


@pytest.mark.parametrize("kind", ["narration", "dialogue"])
def test_speech_beat_without_text_is_an_error(kind):
    scenes = [Scene(id="opening", beats=[Beat(kind=kind, speaker="gm")])]
    report = validate_pack(make_pack(scenes))

    assert any("text" in error for error in report.errors)


def test_pane_beat_without_show_is_an_error():
    scenes = [Scene(id="opening", beats=[Beat(kind="pane")])]
    report = validate_pack(make_pack(scenes))

    assert any("show" in error for error in report.errors)


def test_unknown_beat_kind_is_an_error():
    scenes = [Scene(id="opening", beats=[Beat(kind="interpretive_dance")])]
    report = validate_pack(make_pack(scenes))

    assert any("interpretive_dance" in error for error in report.errors)


def test_pane_beat_needs_no_speaker():
    scenes = [Scene(id="opening", beats=[Beat(kind="pane", show="map")])]

    assert validate_pack(make_pack(scenes)).ok is True


def test_pane_beat_with_an_unknown_speaker_is_still_an_error():
    """A pane beat may omit its speaker, but it may not invent one."""
    scenes = [
        Scene(id="opening", beats=[Beat(kind="pane", show="map", speaker="ghost")]),
    ]
    report = validate_pack(make_pack(scenes))

    assert any("ghost" in error for error in report.errors)


def test_errors_identify_the_scene_they_came_from():
    scenes = [
        Scene(id="opening", beats=[narration()], default_next="act-two"),
        Scene(id="act-two", beats=[narration(speaker="ghost")]),
    ]
    report = validate_pack(make_pack(scenes))

    assert any("act-two" in error and "ghost" in error for error in report.errors)


# ── warnings: suspicious but survivable ──────────────────────────────────────
def test_unreachable_scene_is_a_warning_not_an_error():
    scenes = [
        Scene(id="opening", beats=[narration()]),
        Scene(id="orphan", beats=[narration()]),
    ]
    report = validate_pack(make_pack(scenes))

    assert report.ok is True
    assert any("orphan" in warning for warning in report.warnings)


def test_scene_with_no_beats_is_a_warning():
    scenes = [Scene(id="opening", beats=[])]
    report = validate_pack(make_pack(scenes))

    assert report.ok is True
    assert any("opening" in warning for warning in report.warnings)


def test_scene_reachable_via_branch_is_not_warned_about():
    scenes = [
        Scene(id="opening", beats=[narration()],
              branches=[Branch(id="win", next="victory")]),
        Scene(id="victory", beats=[narration()]),
    ]
    report = validate_pack(make_pack(scenes))

    assert not any("victory" in warning for warning in report.warnings)


def test_start_scene_is_never_reported_unreachable():
    scenes = [Scene(id="opening", beats=[narration()])]
    report = validate_pack(make_pack(scenes))

    assert not any("opening" in warning for warning in report.warnings)


def test_unreachable_scene_that_loops_on_itself_is_still_warned():
    """Self-reference is not arrival — an island stays an island."""
    scenes = [
        Scene(id="opening", beats=[narration()]),
        Scene(id="orphan", beats=[narration()], default_next="orphan"),
    ]
    report = validate_pack(make_pack(scenes))

    assert report.ok is True
    assert any("orphan" in warning for warning in report.warnings)


def test_scene_reachable_only_from_an_unreachable_scene_is_also_warned():
    """Reachability is walked from the start scene, not counted as inbound edges."""
    scenes = [
        Scene(id="opening", beats=[narration()]),
        Scene(id="island-a", beats=[narration()], default_next="island-b"),
        Scene(id="island-b", beats=[narration()]),
    ]
    report = validate_pack(make_pack(scenes))

    joined = " ".join(report.warnings)
    assert "island-a" in joined and "island-b" in joined


def test_reachability_follows_chains_from_the_start_scene():
    scenes = [
        Scene(id="opening", beats=[narration()], default_next="middle"),
        Scene(id="middle", beats=[narration()],
              branches=[Branch(id="on", next="end")]),
        Scene(id="end", beats=[narration()]),
    ]
    report = validate_pack(make_pack(scenes))

    assert not any("unreachable" in warning for warning in report.warnings)


# ── reporting behaviour ──────────────────────────────────────────────────────
def test_validation_logs_a_summary(caplog):
    """Operators read the log, not the return value, when a show fails to start."""
    scenes = [Scene(id="opening", beats=[narration()], default_next="nowhere")]

    with caplog.at_level(logging.INFO, logger="campaign.validator"):
        validate_pack(make_pack(scenes))

    summaries = [r for r in caplog.records if r.levelno == logging.INFO]
    assert summaries, "expected one INFO summary line from validate_pack"
    assert any("1" in record.getMessage() for record in summaries)


def test_report_order_is_stable_across_runs():
    scenes = [
        Scene(id="zebra", beats=[narration(speaker="ghost")]),
        Scene(id="opening", beats=[narration(speaker="phantom")],
              default_next="zebra"),
        Scene(id="alpha", beats=[narration(speaker="wraith")]),
    ]
    pack = make_pack(scenes)

    assert validate_pack(pack).errors == validate_pack(pack).errors


def test_cast_member_who_never_speaks_is_a_warning():
    cast = {
        "gm": CastMember(id="gm", name="GM", role="gm"),
        "alice": CastMember(id="alice", name="Alice", role="player"),
        "silent": CastMember(id="silent", name="Silent Bob", role="player"),
    }
    scenes = [Scene(id="opening", beats=[narration(), narration(speaker="alice")])]
    report = validate_pack(make_pack(scenes, cast=cast))

    assert report.ok is True
    assert any("silent" in warning for warning in report.warnings)
