"""Tests for app/campaign/runtime.py — playing a campaign from start to finish.

The runtime is the thin piece that holds a *position* in the show: which scene
is current, what the show knows so far, which scenes have already played, and
which pass through the loop this is. It drives the renderer over the current
scene, asks the scene graph what comes next, and can write its position to disk
so a crashed or restarted show resumes where it stopped rather than from the
top.

Two seams matter here and are pinned by tests:

  * the renderer is INJECTED and OPTIONAL — with none, the runtime still walks
    the graph, which is how `--validate` and branch-reachability checks work;
  * `carry` survives a reset while everything else is wiped, which is the hook
    the deferred weekly-loop work (the sage who remembers across resets) will
    hang off.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from campaign.pack import Beat, Branch, CampaignPack, CastMember, Scene  # noqa: E402
from campaign.runtime import (  # noqa: E402
    CampaignRuntime, CampaignRuntimeError, CampaignState,
)
from campaign.scene_graph import (  # noqa: E402
    ForcedSelector, SceneGraph, SceneGraphError,
)


# ── helpers ──────────────────────────────────────────────────────────────────
def make_pack(scenes, start_scene="opening", name="testpack"):
    cast = {"gm": CastMember(id="gm", name="GM", role="gm")}
    return CampaignPack(
        name=name, title="Test", genre="fantasy", start_scene=start_scene,
        gm_id="gm", player_ids=[], primitives=[], theme={}, cast=cast,
        scenes={scene.id: scene for scene in scenes},
        root=Path("/nonexistent"), lore_dir=None,
    )


def beat(text="Something happens."):
    return Beat(kind="narration", speaker="gm", text=text)


LINEAR = [
    Scene(id="opening", beats=[beat("It begins.")], default_next="middle"),
    Scene(id="middle", beats=[beat("It continues.")], default_next="ending"),
    Scene(id="ending", beats=[beat("It ends.")]),
]

FORK = [
    Scene(id="opening", beats=[beat()],
          branches=[Branch(id="win", next="victory", when={"outcome": "success"}),
                    Branch(id="lose", next="defeat", when={"outcome": "failure"})],
          default_next="victory"),
    Scene(id="victory", beats=[beat("You win.")]),
    Scene(id="defeat", beats=[beat("You lose.")]),
]

LOOP = [
    Scene(id="opening", beats=[beat("Monday again.")], default_next="opening"),
]


class FakeRenderer:
    """Records which scenes it was asked to render, in order."""

    def __init__(self, raises=None):
        self.scenes = []
        self.contexts = []
        self._raises = raises

    def render_scene(self, scene, context=None):
        self.scenes.append(scene.id)
        self.contexts.append(context)
        if self._raises is not None:
            raise self._raises
        return [f"{scene.id}:{b.text}" for b in scene.beats]


# ── construction and initial position ────────────────────────────────────────
def test_a_new_runtime_sits_on_the_start_scene():
    runtime = CampaignRuntime(make_pack(LINEAR))

    assert runtime.state.scene_id == "opening"
    assert runtime.current_scene.id == "opening"


def test_a_new_runtime_has_played_nothing_yet():
    runtime = CampaignRuntime(make_pack(LINEAR))

    assert runtime.state.history == []
    assert runtime.state.finished is False


def test_a_new_runtime_starts_on_loop_one():
    runtime = CampaignRuntime(make_pack(LINEAR))

    assert runtime.state.loop == 1


def test_state_records_the_campaign_name():
    runtime = CampaignRuntime(make_pack(LINEAR, name="ashiorid"))

    assert runtime.state.campaign == "ashiorid"


def test_runtime_builds_its_own_graph_when_none_is_given():
    runtime = CampaignRuntime(make_pack(LINEAR))

    assert isinstance(runtime.graph, SceneGraph)


def test_an_injected_graph_is_used_as_is():
    pack = make_pack(LINEAR)
    graph = SceneGraph(pack)

    runtime = CampaignRuntime(pack, graph=graph)

    assert runtime.graph is graph


def test_a_pack_whose_start_scene_is_missing_is_rejected_at_construction():
    pack = make_pack(LINEAR, start_scene="nowhere")

    with pytest.raises(SceneGraphError):
        CampaignRuntime(pack)


# ── playing a scene ──────────────────────────────────────────────────────────
def test_play_scene_renders_the_current_scene():
    renderer = FakeRenderer()
    runtime = CampaignRuntime(make_pack(LINEAR), renderer=renderer)

    runtime.play_scene()

    assert renderer.scenes == ["opening"]


def test_play_scene_returns_what_the_renderer_produced():
    runtime = CampaignRuntime(make_pack(LINEAR), renderer=FakeRenderer())

    rendered = runtime.play_scene()

    assert rendered == ["opening:It begins."]


def test_play_scene_records_the_scene_in_history():
    runtime = CampaignRuntime(make_pack(LINEAR), renderer=FakeRenderer())

    runtime.play_scene()

    assert runtime.state.history == ["opening"]


def test_play_scene_passes_the_show_context_to_the_renderer():
    renderer = FakeRenderer()
    runtime = CampaignRuntime(make_pack(LINEAR), renderer=renderer)
    runtime.update_context({"outcome": "success"})

    runtime.play_scene()

    assert renderer.contexts[0] == {"outcome": "success"}


def test_play_scene_without_a_renderer_still_records_history():
    runtime = CampaignRuntime(make_pack(LINEAR))

    rendered = runtime.play_scene()

    assert rendered == []
    assert runtime.state.history == ["opening"]


def test_play_scene_on_a_finished_show_raises():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.state.finished = True

    with pytest.raises(CampaignRuntimeError):
        runtime.play_scene()


# ── advancing ────────────────────────────────────────────────────────────────
def test_advance_moves_to_the_default_next_scene():
    runtime = CampaignRuntime(make_pack(LINEAR))

    assert runtime.advance() == "middle"
    assert runtime.state.scene_id == "middle"


def test_advance_follows_a_branch_matching_the_context():
    runtime = CampaignRuntime(make_pack(FORK))
    runtime.update_context({"outcome": "failure"})

    assert runtime.advance() == "defeat"


def test_advance_at_a_terminal_scene_finishes_the_show():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.state.scene_id = "ending"

    assert runtime.advance() is None
    assert runtime.state.finished is True


def test_a_finished_show_has_no_current_scene():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.state.scene_id = "ending"
    runtime.advance()

    assert runtime.current_scene is None


def test_advance_on_a_finished_show_raises():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.state.finished = True

    with pytest.raises(CampaignRuntimeError):
        runtime.advance()


def test_an_injected_selector_overrides_the_context():
    runtime = CampaignRuntime(make_pack(FORK), selector=ForcedSelector("lose"))

    assert runtime.advance() == "defeat"


def test_a_per_call_selector_beats_the_runtime_default():
    runtime = CampaignRuntime(make_pack(FORK))
    runtime.update_context({"outcome": "success"})

    assert runtime.advance(selector=ForcedSelector("lose")) == "defeat"


# ── context ──────────────────────────────────────────────────────────────────
def test_update_context_merges_rather_than_replaces():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.update_context({"outcome": "success"})
    runtime.update_context({"hp": 3})

    assert runtime.state.context == {"outcome": "success", "hp": 3}


def test_update_context_overwrites_a_key_it_already_had():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.update_context({"outcome": "success"})
    runtime.update_context({"outcome": "failure"})

    assert runtime.state.context["outcome"] == "failure"


# ── step and run ─────────────────────────────────────────────────────────────
def test_step_plays_the_scene_then_moves_on():
    renderer = FakeRenderer()
    runtime = CampaignRuntime(make_pack(LINEAR), renderer=renderer)

    rendered, next_id = runtime.step()

    assert renderer.scenes == ["opening"]
    assert next_id == "middle"
    assert rendered == ["opening:It begins."]


def test_run_plays_every_scene_on_a_linear_path_in_order():
    renderer = FakeRenderer()
    runtime = CampaignRuntime(make_pack(LINEAR), renderer=renderer)

    runtime.run()

    assert renderer.scenes == ["opening", "middle", "ending"]


def test_run_returns_the_scenes_it_played():
    runtime = CampaignRuntime(make_pack(LINEAR))

    assert runtime.run() == ["opening", "middle", "ending"]


def test_run_leaves_the_show_finished():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.run()

    assert runtime.state.finished is True


def test_run_takes_the_branch_the_context_selects():
    runtime = CampaignRuntime(make_pack(FORK))
    runtime.update_context({"outcome": "failure"})

    assert runtime.run() == ["opening", "defeat"]


def test_run_stops_at_the_scene_budget_instead_of_hanging_on_a_loop():
    runtime = CampaignRuntime(make_pack(LOOP))

    played = runtime.run(max_scenes=5)

    assert played == ["opening"] * 5


def test_hitting_the_budget_does_not_mark_the_show_finished():
    runtime = CampaignRuntime(make_pack(LOOP))
    runtime.run(max_scenes=3)

    assert runtime.state.finished is False


def test_hitting_the_budget_logs_a_warning(caplog):
    runtime = CampaignRuntime(make_pack(LOOP))

    with caplog.at_level(logging.WARNING, logger="campaign.runtime"):
        runtime.run(max_scenes=2)

    assert any("budget" in r.message.lower() or "max" in r.message.lower()
               for r in caplog.records)


def test_finishing_exactly_on_the_budget_does_not_warn(caplog):
    runtime = CampaignRuntime(make_pack(LINEAR))

    with caplog.at_level(logging.WARNING, logger="campaign.runtime"):
        runtime.run(max_scenes=3)

    assert runtime.state.finished is True
    assert caplog.records == []


def test_run_resumes_from_wherever_the_runtime_currently_sits():
    renderer = FakeRenderer()
    runtime = CampaignRuntime(make_pack(LINEAR), renderer=renderer)
    runtime.state.scene_id = "middle"

    runtime.run()

    assert renderer.scenes == ["middle", "ending"]


# ── reset and the weekly loop seam ───────────────────────────────────────────
def test_reset_returns_to_the_start_scene():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.run()

    runtime.reset()

    assert runtime.state.scene_id == "opening"
    assert runtime.state.finished is False


def test_reset_clears_history_and_context():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.update_context({"outcome": "success"})
    runtime.run()

    runtime.reset()

    assert runtime.state.history == []
    assert runtime.state.context == {}


def test_reset_increments_the_loop_counter():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.reset()

    assert runtime.state.loop == 2


def test_carry_survives_a_reset():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.state.carry["sage_remembers"] = ["the portal"]

    runtime.reset()

    assert runtime.state.carry == {"sage_remembers": ["the portal"]}


def test_reset_can_wipe_carry_when_asked():
    runtime = CampaignRuntime(make_pack(LINEAR))
    runtime.state.carry["sage_remembers"] = ["the portal"]

    runtime.reset(keep_carry=False)

    assert runtime.state.carry == {}


# ── persistence ──────────────────────────────────────────────────────────────
def test_save_writes_a_state_file(tmp_path):
    path = tmp_path / "position.json"
    runtime = CampaignRuntime(make_pack(LINEAR), state_file=path)

    runtime.save()

    assert path.exists()


def test_saved_state_is_json_carrying_the_position(tmp_path):
    path = tmp_path / "position.json"
    runtime = CampaignRuntime(make_pack(LINEAR), state_file=path)
    runtime.play_scene()
    runtime.advance()

    runtime.save()

    data = json.loads(path.read_text())
    assert data["scene_id"] == "middle"
    assert data["history"] == ["opening"]
    assert data["campaign"] == "testpack"


def test_save_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "deep" / "nested" / "position.json"
    runtime = CampaignRuntime(make_pack(LINEAR), state_file=path)

    runtime.save()

    assert path.exists()


def test_save_without_a_state_file_raises():
    runtime = CampaignRuntime(make_pack(LINEAR))

    with pytest.raises(CampaignRuntimeError):
        runtime.save()


def test_save_accepts_an_explicit_path(tmp_path):
    path = tmp_path / "elsewhere.json"
    runtime = CampaignRuntime(make_pack(LINEAR))

    runtime.save(path)

    assert path.exists()


def test_load_restores_the_position(tmp_path):
    path = tmp_path / "position.json"
    pack = make_pack(LINEAR)
    first = CampaignRuntime(pack, state_file=path)
    first.update_context({"outcome": "failure"})
    first.play_scene()
    first.advance()
    first.save()

    second = CampaignRuntime(pack, state_file=path)
    second.load()

    assert second.state.scene_id == "middle"
    assert second.state.history == ["opening"]
    assert second.state.context == {"outcome": "failure"}


def test_load_restores_the_loop_counter_and_carry(tmp_path):
    path = tmp_path / "position.json"
    pack = make_pack(LINEAR)
    first = CampaignRuntime(pack, state_file=path)
    first.reset()
    first.state.carry["sage_remembers"] = ["the portal"]
    first.save()

    second = CampaignRuntime(pack, state_file=path)
    second.load()

    assert second.state.loop == 2
    assert second.state.carry == {"sage_remembers": ["the portal"]}


def test_load_of_a_missing_file_raises(tmp_path):
    runtime = CampaignRuntime(make_pack(LINEAR), state_file=tmp_path / "gone.json")

    with pytest.raises(CampaignRuntimeError):
        runtime.load()


def test_load_of_a_corrupt_file_raises(tmp_path):
    path = tmp_path / "position.json"
    path.write_text("{not json at all")
    runtime = CampaignRuntime(make_pack(LINEAR), state_file=path)

    with pytest.raises(CampaignRuntimeError):
        runtime.load()


def test_load_of_json_that_is_not_a_mapping_raises(tmp_path):
    path = tmp_path / "position.json"
    path.write_text("[1, 2, 3]")
    runtime = CampaignRuntime(make_pack(LINEAR), state_file=path)

    with pytest.raises(CampaignRuntimeError):
        runtime.load()


def test_load_of_a_payload_missing_the_campaign_key_raises(tmp_path):
    path = tmp_path / "position.json"
    path.write_text('{"scene_id": "middle"}')
    runtime = CampaignRuntime(make_pack(LINEAR), state_file=path)

    with pytest.raises(CampaignRuntimeError):
        runtime.load()


def test_load_of_a_payload_missing_the_scene_id_key_raises(tmp_path):
    path = tmp_path / "position.json"
    path.write_text('{"campaign": "testpack"}')
    runtime = CampaignRuntime(make_pack(LINEAR), state_file=path)

    with pytest.raises(CampaignRuntimeError):
        runtime.load()


def test_load_of_state_for_a_different_campaign_raises(tmp_path):
    path = tmp_path / "position.json"
    CampaignRuntime(make_pack(LINEAR, name="other"), state_file=path).save()

    runtime = CampaignRuntime(make_pack(LINEAR, name="ashiorid"), state_file=path)

    with pytest.raises(CampaignRuntimeError, match="ashiorid|other|campaign"):
        runtime.load()


def test_load_of_state_naming_an_unknown_scene_raises(tmp_path):
    path = tmp_path / "position.json"
    runtime = CampaignRuntime(make_pack(LINEAR), state_file=path)
    runtime.state.scene_id = "middle"
    runtime.save()
    path.write_text(path.read_text().replace('"middle"', '"nowhere"'))

    with pytest.raises(CampaignRuntimeError):
        runtime.load()


def test_a_resumed_show_continues_from_where_it_stopped(tmp_path):
    path = tmp_path / "position.json"
    pack = make_pack(LINEAR)
    first = CampaignRuntime(pack, renderer=FakeRenderer(), state_file=path)
    first.step()
    first.save()

    renderer = FakeRenderer()
    second = CampaignRuntime(pack, renderer=renderer, state_file=path)
    second.load()
    second.run()

    assert renderer.scenes == ["middle", "ending"]


def test_a_finished_show_round_trips_as_finished(tmp_path):
    path = tmp_path / "position.json"
    pack = make_pack(LINEAR)
    first = CampaignRuntime(pack, state_file=path)
    first.run()
    first.save()

    second = CampaignRuntime(pack, state_file=path)
    second.load()

    assert second.state.finished is True
    assert second.state.scene_id is None


# ── CampaignState on its own ─────────────────────────────────────────────────
def test_state_round_trips_through_a_dict():
    state = CampaignState(campaign="ashiorid", scene_id="party-attack",
                          context={"outcome": "success"}, history=["invitation"],
                          loop=3, carry={"sage": 1}, finished=False)

    restored = CampaignState.from_dict(state.to_dict())

    assert restored == state


def test_state_to_dict_is_json_serializable():
    state = CampaignState(campaign="ashiorid", scene_id="opening")

    json.dumps(state.to_dict())


def test_state_defaults_are_empty_and_independent():
    first = CampaignState(campaign="a", scene_id="x")
    second = CampaignState(campaign="b", scene_id="y")
    first.history.append("x")
    first.context["k"] = 1
    first.carry["c"] = 1

    assert second.history == []
    assert second.context == {}
    assert second.carry == {}


def test_from_dict_tolerates_a_minimal_payload():
    state = CampaignState.from_dict({"campaign": "ashiorid", "scene_id": "opening"})

    assert state.loop == 1
    assert state.history == []
    assert state.finished is False
