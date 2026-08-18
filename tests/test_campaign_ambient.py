"""Tests for app/campaign/ambient.py — the filler-scene scheduler.

Ambient scenes are where the hours actually come from. The authored spine is
~15 minutes; a week of stream is filled by injecting generated filler between
plot scenes. This module decides *when* to inject and *which* scene to use.

It makes no LLM calls and reads no files — it is pure scheduling, so it is
fully deterministic under an injected random.Random.
"""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from campaign.ambient import AmbientScheduler  # noqa: E402
from campaign.pack import CampaignPack, CastMember, Scene  # noqa: E402


def make_pack(ambient_ids=("camp-fire", "road-talk", "night-watch"),
              spine_ids=("opening", "victory"), ambient_every=2, ambient_pool=None):
    scenes = {}
    for scene_id in spine_ids:
        scenes[scene_id] = Scene(id=scene_id)
    for scene_id in ambient_ids:
        scenes[scene_id] = Scene(id=scene_id, ambient=True, prompt="They wait.")
    return CampaignPack(
        name="testpack", title="Test", genre="fantasy", start_scene="opening",
        gm_id="gm", player_ids=["helen"], primitives=[], theme={},
        cast={"gm": CastMember(id="gm", name="GM", role="gm"),
              "helen": CastMember(id="helen", name="Helen", role="player")},
        scenes=scenes, root=Path("/nonexistent"), lore_dir=None, lore={},
        ambient_every=ambient_every, ambient_pool=list(ambient_pool or []),
    )


# ── construction ─────────────────────────────────────────────────────────────
def test_scheduler_constructs_from_a_pack_alone():
    scheduler = AmbientScheduler(make_pack())

    assert scheduler.pool
    assert scheduler.last is None


def test_the_pool_defaults_to_the_packs_ambient_scenes():
    scheduler = AmbientScheduler(make_pack())

    assert sorted(scheduler.pool) == ["camp-fire", "night-watch", "road-talk"]


def test_an_explicit_pool_overrides_the_packs():
    scheduler = AmbientScheduler(make_pack(), pool=["road-talk"])

    assert scheduler.pool == ["road-talk"]


def test_the_pool_is_a_copy_not_an_alias():
    supplied = ["road-talk", "camp-fire"]
    scheduler = AmbientScheduler(make_pack(), pool=supplied)

    supplied.append("night-watch")

    assert scheduler.pool == ["road-talk", "camp-fire"]


def test_every_defaults_to_two():
    assert AmbientScheduler(make_pack()).every == 2


def test_an_explicit_every_overrides_the_default():
    assert AmbientScheduler(make_pack(), every=5).every == 5


# ── should_inject ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("played, expected", [
    (0, False), (1, False), (2, True), (3, False), (4, True),
    (5, False), (6, True), (100, True), (101, False),
])
def test_injection_lands_on_every_nth_spine_scene(played, expected):
    scheduler = AmbientScheduler(make_pack(), every=2)

    assert scheduler.should_inject(played) is expected


def test_every_of_one_injects_after_every_scene():
    scheduler = AmbientScheduler(make_pack(), every=1)

    assert [scheduler.should_inject(n) for n in range(1, 5)] == [True] * 4


def test_zero_at_the_start_is_never_an_injection_point():
    # 0 % N == 0 for every N; injecting before the show has played anything
    # would open the stream on filler instead of the cold open.
    assert AmbientScheduler(make_pack(), every=1).should_inject(0) is False


@pytest.mark.parametrize("every", [0, -1, None])
def test_a_disabled_or_invalid_every_never_injects(every):
    scheduler = AmbientScheduler(make_pack(), every=every)

    assert [scheduler.should_inject(n) for n in range(0, 10)] == [False] * 10


def test_an_empty_pool_never_injects():
    scheduler = AmbientScheduler(make_pack(ambient_ids=()), every=1)

    assert scheduler.should_inject(2) is False


def test_should_inject_returns_a_real_bool():
    # the runtime branches on this and state is serialised to JSON
    assert isinstance(AmbientScheduler(make_pack()).should_inject(2), bool)


# ── pick ─────────────────────────────────────────────────────────────────────
def test_pick_returns_a_scene_from_the_pool():
    scheduler = AmbientScheduler(make_pack())

    assert scheduler.pick() in {"camp-fire", "road-talk", "night-watch"}


def test_pick_returns_none_on_an_empty_pool():
    assert AmbientScheduler(make_pack(ambient_ids=())).pick() is None


def test_pick_records_what_it_returned():
    scheduler = AmbientScheduler(make_pack())

    assert scheduler.pick() == scheduler.last


def test_pick_never_repeats_while_alternatives_exist():
    scheduler = AmbientScheduler(make_pack(), rng=random.Random(1))

    picks = [scheduler.pick() for _ in range(40)]

    assert all(a != b for a, b in zip(picks, picks[1:]))


def test_a_single_entry_pool_repeats_because_it_must():
    scheduler = AmbientScheduler(make_pack(), pool=["road-talk"])

    assert [scheduler.pick() for _ in range(3)] == ["road-talk"] * 3


def test_every_scene_in_the_pool_eventually_airs():
    scheduler = AmbientScheduler(make_pack(), rng=random.Random(3))

    picks = {scheduler.pick() for _ in range(60)}

    assert picks == {"camp-fire", "road-talk", "night-watch"}


def test_picking_is_deterministic_under_a_seeded_rng():
    first = AmbientScheduler(make_pack(), rng=random.Random(11))
    second = AmbientScheduler(make_pack(), rng=random.Random(11))

    assert [first.pick() for _ in range(20)] == [second.pick() for _ in range(20)]


def test_different_seeds_diverge():
    first = AmbientScheduler(make_pack(), rng=random.Random(1))
    second = AmbientScheduler(make_pack(), rng=random.Random(99))

    assert [first.pick() for _ in range(20)] != [second.pick() for _ in range(20)]


def test_the_scheduler_works_without_an_injected_rng():
    scheduler = AmbientScheduler(make_pack())

    assert all(scheduler.pick() is not None for _ in range(10))


def test_an_explicit_pack_pool_is_honoured():
    pack = make_pack(ambient_pool=["road-talk", "camp-fire"])
    scheduler = AmbientScheduler(pack)

    assert sorted(scheduler.pool) == ["camp-fire", "road-talk"]


def test_pick_does_not_mutate_the_pool():
    scheduler = AmbientScheduler(make_pack())
    before = list(scheduler.pool)

    for _ in range(10):
        scheduler.pick()

    assert scheduler.pool == before
