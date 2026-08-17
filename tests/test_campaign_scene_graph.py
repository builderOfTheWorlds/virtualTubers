"""Tests for app/campaign/scene_graph.py — how a campaign chooses what happens next.

A pack's scenes form a directed graph. This module walks it: given the scene
just played and whatever the show knows (dice outcomes, flags, later chat
votes), it picks the next scene.

The selector is the deliberate seam. `ScriptedSelector` ships now and keeps a
show reproducible; `WeightedRandomSelector` ships for variety; a future
`ChatVoteSelector` plugs into the same interface without the runtime changing.
Cycles are expected — the premise of the show is a loop — so traversal is
bounded by a step budget rather than by cycle detection.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from campaign.pack import Beat, Branch, CampaignPack, CastMember, Scene  # noqa: E402
from campaign.scene_graph import (  # noqa: E402
    BranchSelector, ForcedSelector, SceneGraph, SceneGraphError,
    ScriptedSelector, WeightedRandomSelector,
)


# ── helpers ──────────────────────────────────────────────────────────────────
def make_pack(scenes, start_scene="opening"):
    cast = {"gm": CastMember(id="gm", name="GM", role="gm")}
    return CampaignPack(
        name="testpack", title="Test", genre="fantasy", start_scene=start_scene,
        gm_id="gm", player_ids=[], primitives=[], theme={}, cast=cast,
        scenes={scene.id: scene for scene in scenes},
        root=Path("/nonexistent"), lore_dir=None,
    )


def beat(text="Something happens."):
    return Beat(kind="narration", speaker="gm", text=text)


FORK = [
    Scene(id="opening", beats=[beat()],
          branches=[Branch(id="win", next="victory", when={"outcome": "success"}),
                    Branch(id="lose", next="defeat", when={"outcome": "failure"})],
          default_next="victory"),
    Scene(id="victory", beats=[beat("You win.")]),
    Scene(id="defeat", beats=[beat("You lose.")]),
]


# ── graph construction ───────────────────────────────────────────────────────
def test_graph_exposes_the_start_scene():
    graph = SceneGraph(make_pack(FORK))

    assert graph.start.id == "opening"


def test_graph_rejects_a_pack_whose_start_scene_is_missing():
    with pytest.raises(SceneGraphError, match="nowhere"):
        SceneGraph(make_pack(FORK, start_scene="nowhere"))


def test_successors_lists_branch_targets_and_default():
    graph = SceneGraph(make_pack(FORK))

    assert sorted(graph.successors("opening")) == ["defeat", "victory"]


def test_successors_of_a_terminal_scene_is_empty():
    graph = SceneGraph(make_pack(FORK))

    assert graph.successors("victory") == []


def test_successors_of_an_unknown_scene_names_it():
    graph = SceneGraph(make_pack(FORK))

    with pytest.raises(SceneGraphError, match="atlantis"):
        graph.successors("atlantis")


def test_successors_does_not_duplicate_a_default_that_is_also_a_branch():
    graph = SceneGraph(make_pack(FORK))

    assert graph.successors("opening").count("victory") == 1


# ── scripted selection ───────────────────────────────────────────────────────
def test_scripted_selector_matches_a_branch_condition():
    graph = SceneGraph(make_pack(FORK))

    assert graph.next_scene_id("opening", context={"outcome": "failure"}) == "defeat"


def test_scripted_selector_falls_back_to_default_next_when_nothing_matches():
    graph = SceneGraph(make_pack(FORK))

    assert graph.next_scene_id("opening", context={"outcome": "unresolved"}) == "victory"


def test_no_context_falls_back_to_default_next():
    graph = SceneGraph(make_pack(FORK))

    assert graph.next_scene_id("opening") == "victory"


def test_terminal_scene_has_no_next():
    graph = SceneGraph(make_pack(FORK))

    assert graph.next_scene_id("victory") is None


def test_a_branch_with_no_condition_always_matches():
    scenes = [
        Scene(id="opening", beats=[beat()], branches=[Branch(id="onward", next="end")]),
        Scene(id="end", beats=[beat()]),
    ]
    graph = SceneGraph(make_pack(scenes))

    assert graph.next_scene_id("opening") == "end"


def test_condition_requires_every_key_to_match():
    scenes = [
        Scene(id="opening", beats=[beat()],
              branches=[Branch(id="both", next="end",
                               when={"outcome": "success", "loop": 3})],
              default_next="opening"),
        Scene(id="end", beats=[beat()]),
    ]
    graph = SceneGraph(make_pack(scenes))

    assert graph.next_scene_id("opening", {"outcome": "success", "loop": 3}) == "end"
    assert graph.next_scene_id("opening", {"outcome": "success", "loop": 1}) == "opening"


def test_weight_is_a_reserved_key_and_not_a_condition():
    """`when.weight` steers WeightedRandomSelector; it must not gate matching."""
    scenes = [
        Scene(id="opening", beats=[beat()],
              branches=[Branch(id="onward", next="end", when={"weight": 5})]),
        Scene(id="end", beats=[beat()]),
    ]
    graph = SceneGraph(make_pack(scenes))

    assert graph.next_scene_id("opening", context={}) == "end"


def test_earlier_branch_wins_when_two_match():
    scenes = [
        Scene(id="opening", beats=[beat()],
              branches=[Branch(id="first", next="a"), Branch(id="second", next="b")]),
        Scene(id="a", beats=[beat()]),
        Scene(id="b", beats=[beat()]),
    ]
    graph = SceneGraph(make_pack(scenes))

    assert graph.next_scene_id("opening") == "a"


def test_branch_pointing_at_an_unknown_scene_is_an_error():
    scenes = [Scene(id="opening", beats=[beat()],
                    branches=[Branch(id="win", next="atlantis")])]
    graph = SceneGraph(make_pack(scenes))

    with pytest.raises(SceneGraphError, match="atlantis"):
        graph.next_scene_id("opening")


def test_scripted_selection_is_deterministic():
    graph = SceneGraph(make_pack(FORK))
    context = {"outcome": "failure"}

    assert {graph.next_scene_id("opening", context) for _ in range(10)} == {"defeat"}


# ── selectors as a pluggable seam ────────────────────────────────────────────
def test_selector_interface_is_subclassable():
    class AlwaysLast(BranchSelector):
        def select(self, scene, branches, context):
            return branches[-1].id

    graph = SceneGraph(make_pack(FORK), selector=AlwaysLast())

    assert graph.next_scene_id("opening", {"outcome": "success"}) == "defeat"


def test_a_selector_returning_none_falls_back_to_default_next():
    class Abstains(BranchSelector):
        def select(self, scene, branches, context):
            return None

    graph = SceneGraph(make_pack(FORK), selector=Abstains())

    assert graph.next_scene_id("opening", {"outcome": "failure"}) == "victory"


def test_a_selector_naming_an_unknown_branch_is_an_error():
    class Nonsense(BranchSelector):
        def select(self, scene, branches, context):
            return "not_a_branch"

    graph = SceneGraph(make_pack(FORK), selector=Nonsense())

    with pytest.raises(SceneGraphError, match="not_a_branch"):
        graph.next_scene_id("opening")


def test_per_call_selector_overrides_the_graph_default():
    graph = SceneGraph(make_pack(FORK))

    result = graph.next_scene_id("opening", {"outcome": "success"},
                                 selector=ForcedSelector("lose"))

    assert result == "defeat"


def test_selectors_are_not_consulted_for_a_scene_with_no_branches():
    class Explodes(BranchSelector):
        def select(self, scene, branches, context):
            raise AssertionError("should not be called")

    scenes = [
        Scene(id="opening", beats=[beat()], default_next="end"),
        Scene(id="end", beats=[beat()]),
    ]
    graph = SceneGraph(make_pack(scenes), selector=Explodes())

    assert graph.next_scene_id("opening") == "end"


# ── forced selection (the --force-branch CLI flag) ───────────────────────────
def test_forced_selector_takes_its_branch_regardless_of_context():
    graph = SceneGraph(make_pack(FORK), selector=ForcedSelector("lose"))

    assert graph.next_scene_id("opening", {"outcome": "success"}) == "defeat"


def test_forced_selector_defers_when_the_scene_lacks_that_branch():
    scenes = [
        Scene(id="opening", beats=[beat()],
              branches=[Branch(id="onward", next="end")]),
        Scene(id="end", beats=[beat()]),
    ]
    graph = SceneGraph(make_pack(scenes), selector=ForcedSelector("lose"))

    assert graph.next_scene_id("opening") == "end"


# ── weighted random selection ────────────────────────────────────────────────
def test_weighted_selector_is_reproducible_from_a_seed():
    graph_a = SceneGraph(make_pack(FORK), selector=WeightedRandomSelector(seed=1234))
    graph_b = SceneGraph(make_pack(FORK), selector=WeightedRandomSelector(seed=1234))

    walk_a = [graph_a.next_scene_id("opening") for _ in range(20)]
    walk_b = [graph_b.next_scene_id("opening") for _ in range(20)]

    assert walk_a == walk_b


def test_weighted_selector_only_picks_real_branches():
    graph = SceneGraph(make_pack(FORK), selector=WeightedRandomSelector(seed=7))

    assert {graph.next_scene_id("opening") for _ in range(30)} <= {"victory", "defeat"}


def test_weighted_selector_honours_a_zero_weight():
    scenes = [
        Scene(id="opening", beats=[beat()],
              branches=[Branch(id="never", next="a", when={"weight": 0}),
                        Branch(id="always", next="b", when={"weight": 5})]),
        Scene(id="a", beats=[beat()]),
        Scene(id="b", beats=[beat()]),
    ]
    graph = SceneGraph(make_pack(scenes), selector=WeightedRandomSelector(seed=3))

    assert {graph.next_scene_id("opening") for _ in range(40)} == {"b"}


def test_weighted_selector_treats_a_missing_weight_as_one():
    scenes = [
        Scene(id="opening", beats=[beat()],
              branches=[Branch(id="a", next="a"), Branch(id="b", next="b")]),
        Scene(id="a", beats=[beat()]),
        Scene(id="b", beats=[beat()]),
    ]
    graph = SceneGraph(make_pack(scenes), selector=WeightedRandomSelector(seed=11))
    picks = [graph.next_scene_id("opening") for _ in range(200)]

    assert set(picks) == {"a", "b"}


# ── traversal ────────────────────────────────────────────────────────────────
def test_walk_yields_scenes_from_the_start():
    scenes = [
        Scene(id="opening", beats=[beat()], default_next="middle"),
        Scene(id="middle", beats=[beat()], default_next="end"),
        Scene(id="end", beats=[beat()]),
    ]
    graph = SceneGraph(make_pack(scenes))

    assert [scene.id for scene in graph.walk()] == ["opening", "middle", "end"]


def test_walk_yields_scene_objects_not_ids():
    graph = SceneGraph(make_pack(FORK))

    assert all(isinstance(scene, Scene) for scene in graph.walk())


def test_walk_follows_the_context_through_a_fork():
    graph = SceneGraph(make_pack(FORK))

    walked = [scene.id for scene in graph.walk(context={"outcome": "failure"})]

    assert walked == ["opening", "defeat"]


def test_walk_can_start_from_an_arbitrary_scene():
    graph = SceneGraph(make_pack(FORK))

    assert [s.id for s in graph.walk(start="defeat")] == ["defeat"]


def test_walk_stops_at_the_step_budget_instead_of_hanging():
    """A time loop is the premise, so traversal must be bounded, not cycle-free."""
    scenes = [Scene(id="opening", beats=[beat()], default_next="opening")]
    graph = SceneGraph(make_pack(scenes))

    assert len(list(graph.walk(max_steps=5))) == 5


def test_walk_is_lazy():
    scenes = [Scene(id="opening", beats=[beat()], default_next="opening")]
    graph = SceneGraph(make_pack(scenes))

    walker = graph.walk(max_steps=10_000_000)

    assert next(iter(walker)).id == "opening"


def test_walk_from_an_unknown_scene_names_it():
    graph = SceneGraph(make_pack(FORK))

    with pytest.raises(SceneGraphError, match="atlantis"):
        list(graph.walk(start="atlantis"))
