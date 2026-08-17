"""Tests for app/campaign/pack.py — loading a campaign pack off disk.

The loader's contract: turn a `campaigns/<name>/` directory into typed
objects (CampaignPack / Scene / Beat / Branch / CastMember), apply documented
defaults for omitted optional fields, and raise PackError — never a bare
KeyError, AttributeError or yaml exception — for anything structurally wrong.

Semantic validation (dangling branch targets, unknown speakers, unknown
primitives) is NOT this module's job; that belongs to campaign.validator.
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from campaign.pack import (  # noqa: E402
    Beat, Branch, CampaignPack, CastMember, PackError, Scene, load_pack,
)


# ── fixtures ─────────────────────────────────────────────────────────────────
CAMPAIGN_YAML = {
    "name": "testpack",
    "title": "A Test Campaign",
    "genre": "fantasy",
    "start_scene": "opening",
    "gm": "gm",
    "players": ["alice", "bob"],
    "primitives": ["roll_check", "cast_spell"],
    "theme": {"palette": "amber"},
}

GM_YAML = {
    "name": "The Chronicler",
    "archetype": "narrator",
    "system_prompt": "You narrate.",
    "voice": "boss",
}

ALICE_YAML = {
    "name": "Alice the Bold",
    "archetype": "paladin",
    "system_prompt": "You are brave.",
    "voice": "coder",
    "avatar": "alice_ascii",
}

BOB_YAML = {"name": "Bob", "archetype": "rogue", "system_prompt": "You skulk."}

OPENING_SCENE = {
    "id": "opening",
    "title": "The Opening",
    "enter_narration": "A hall, ten thousand candles.",
    "beats": [
        {"speaker": "gm", "type": "narration", "text": "The doors open."},
        {"speaker": "alice", "type": "dialogue", "text": "I don't like this.",
         "improv": True},
        {"speaker": "bob", "type": "action", "primitive": "roll_check",
         "params": {"skill": "perception", "dc": 15}},
        {"type": "pane", "show": "map"},
    ],
    "branches": [
        {"id": "success", "when": {"outcome": "success"}, "next": "victory"},
        {"id": "failure", "when": {"outcome": "failure"}, "next": "defeat"},
    ],
    "default_next": "victory",
}

VICTORY_SCENE = {
    "id": "victory",
    "beats": [{"speaker": "gm", "type": "narration", "text": "You win."}],
}


def write_pack(root, campaign=None, scenes=None, cast=None, lore=False):
    """Build a campaign pack on disk and return its root path."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "campaign.yaml").write_text(
        yaml.safe_dump(CAMPAIGN_YAML if campaign is None else campaign),
        encoding="utf-8")

    cast_dir = root / "cast"
    cast_dir.mkdir(exist_ok=True)
    for member_id, body in (cast if cast is not None else
                            {"gm": GM_YAML, "alice": ALICE_YAML, "bob": BOB_YAML}).items():
        (cast_dir / f"{member_id}.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    scenes_dir = root / "scenes"
    scenes_dir.mkdir(exist_ok=True)
    for index, body in enumerate(scenes if scenes is not None
                                 else [OPENING_SCENE, VICTORY_SCENE]):
        name = body.get("id", f"scene{index}") if isinstance(body, dict) else f"scene{index}"
        (scenes_dir / f"{index:02d}-{name}.yaml").write_text(
            yaml.safe_dump(body), encoding="utf-8")

    if lore:
        lore_dir = root / "lore"
        lore_dir.mkdir(exist_ok=True)
        (lore_dir / "history.md").write_text("# History\n", encoding="utf-8")

    return root


@pytest.fixture
def pack_root(tmp_path):
    return write_pack(tmp_path / "testpack")


# ── happy path ───────────────────────────────────────────────────────────────
def test_load_pack_returns_campaign_pack_with_metadata(pack_root):
    pack = load_pack(pack_root)

    assert isinstance(pack, CampaignPack)
    assert pack.name == "testpack"
    assert pack.title == "A Test Campaign"
    assert pack.genre == "fantasy"
    assert pack.start_scene == "opening"
    assert pack.primitives == ["roll_check", "cast_spell"]
    assert pack.theme == {"palette": "amber"}
    assert pack.root == Path(pack_root)


def test_load_pack_indexes_scenes_by_id(pack_root):
    pack = load_pack(pack_root)

    assert set(pack.scenes) == {"opening", "victory"}
    assert all(isinstance(scene, Scene) for scene in pack.scenes.values())
    assert pack.scenes["opening"].title == "The Opening"
    assert pack.scenes["opening"].enter_narration == "A hall, ten thousand candles."


def test_scene_beats_are_typed_with_defaults(pack_root):
    beats = load_pack(pack_root).scenes["opening"].beats

    assert all(isinstance(beat, Beat) for beat in beats)
    assert [beat.kind for beat in beats] == ["narration", "dialogue", "action", "pane"]

    narration, dialogue, action, pane = beats

    assert narration.speaker == "gm"
    assert narration.text == "The doors open."
    # documented defaults for omitted optional fields
    assert narration.improv is False
    assert narration.params == {}
    assert narration.primitive is None
    assert narration.show is None

    assert dialogue.improv is True

    assert action.primitive == "roll_check"
    assert action.params == {"skill": "perception", "dc": 15}

    assert pane.show == "map"
    assert pane.speaker is None


def test_scene_branches_are_typed(pack_root):
    scene = load_pack(pack_root).scenes["opening"]

    assert all(isinstance(branch, Branch) for branch in scene.branches)
    assert [branch.id for branch in scene.branches] == ["success", "failure"]
    assert scene.branches[0].when == {"outcome": "success"}
    assert scene.branches[0].next == "victory"
    assert scene.default_next == "victory"


def test_scene_without_branches_defaults_to_empty_list(pack_root):
    scene = load_pack(pack_root).scenes["victory"]

    assert scene.branches == []
    assert scene.default_next is None
    assert scene.enter_narration is None


def test_cast_is_loaded_and_roles_assigned(pack_root):
    pack = load_pack(pack_root)

    assert set(pack.cast) == {"gm", "alice", "bob"}
    assert all(isinstance(member, CastMember) for member in pack.cast.values())

    assert pack.gm_id == "gm"
    assert pack.player_ids == ["alice", "bob"]          # order follows campaign.yaml
    assert pack.cast["gm"].role == "gm"
    assert pack.cast["alice"].role == "player"

    alice = pack.cast["alice"]
    assert alice.id == "alice"
    assert alice.name == "Alice the Bold"
    assert alice.archetype == "paladin"
    assert alice.system_prompt == "You are brave."
    assert alice.voice == "coder"
    assert alice.avatar == "alice_ascii"


def test_cast_member_optional_fields_default(pack_root):
    bob = load_pack(pack_root).cast["bob"]

    assert bob.voice is None
    assert bob.avatar is None


def test_lore_dir_is_none_when_absent(pack_root):
    assert load_pack(pack_root).lore_dir is None


def test_lore_dir_is_set_when_present(tmp_path):
    root = write_pack(tmp_path / "withlore", lore=True)

    assert load_pack(root).lore_dir == Path(root) / "lore"


def test_load_pack_accepts_a_string_path(pack_root):
    assert load_pack(str(pack_root)).name == "testpack"


# ── lookup helpers ───────────────────────────────────────────────────────────
def test_scene_helper_returns_scene(pack_root):
    pack = load_pack(pack_root)

    assert pack.scene("opening") is pack.scenes["opening"]


def test_scene_helper_raises_pack_error_for_unknown_id(pack_root):
    pack = load_pack(pack_root)

    with pytest.raises(PackError, match="nope"):
        pack.scene("nope")


def test_member_helper_returns_cast_member(pack_root):
    pack = load_pack(pack_root)

    assert pack.member("alice") is pack.cast["alice"]


def test_member_helper_raises_pack_error_for_unknown_id(pack_root):
    pack = load_pack(pack_root)

    with pytest.raises(PackError, match="ghost"):
        pack.member("ghost")


# ── failure modes: every one is a PackError ──────────────────────────────────
def test_missing_pack_directory_raises_pack_error(tmp_path):
    with pytest.raises(PackError):
        load_pack(tmp_path / "does-not-exist")


def test_missing_campaign_yaml_raises_pack_error(tmp_path):
    root = tmp_path / "broken"
    (root / "scenes").mkdir(parents=True)

    with pytest.raises(PackError, match="campaign.yaml"):
        load_pack(root)


def test_malformed_yaml_raises_pack_error(tmp_path):
    root = write_pack(tmp_path / "bad")
    (root / "campaign.yaml").write_text("name: [unclosed\n", encoding="utf-8")

    with pytest.raises(PackError):
        load_pack(root)


def test_campaign_yaml_that_is_not_a_mapping_raises_pack_error(tmp_path):
    root = write_pack(tmp_path / "bad")
    (root / "campaign.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(PackError):
        load_pack(root)


@pytest.mark.parametrize("missing_key", ["name", "start_scene", "gm"])
def test_campaign_yaml_missing_required_key_raises_pack_error(tmp_path, missing_key):
    campaign = {key: value for key, value in CAMPAIGN_YAML.items() if key != missing_key}
    root = write_pack(tmp_path / "bad", campaign=campaign)

    with pytest.raises(PackError, match=missing_key):
        load_pack(root)


def test_missing_scenes_directory_raises_pack_error(tmp_path):
    root = write_pack(tmp_path / "bad")
    for scene_file in (root / "scenes").iterdir():
        scene_file.unlink()
    (root / "scenes").rmdir()

    with pytest.raises(PackError, match="scenes"):
        load_pack(root)


def test_scene_without_id_raises_pack_error(tmp_path):
    root = write_pack(tmp_path / "bad", scenes=[{"beats": []}])

    with pytest.raises(PackError, match="id"):
        load_pack(root)


def test_duplicate_scene_id_raises_pack_error(tmp_path):
    root = write_pack(tmp_path / "bad", scenes=[VICTORY_SCENE, dict(VICTORY_SCENE)])

    with pytest.raises(PackError, match="victory"):
        load_pack(root)


def test_beat_without_type_raises_pack_error(tmp_path):
    scene = {"id": "s", "beats": [{"speaker": "gm", "text": "hi"}]}
    root = write_pack(tmp_path / "bad", scenes=[scene],
                      campaign={**CAMPAIGN_YAML, "start_scene": "s"})

    with pytest.raises(PackError, match="type"):
        load_pack(root)


def test_declared_cast_member_without_a_file_raises_pack_error(tmp_path):
    root = write_pack(tmp_path / "bad", cast={"gm": GM_YAML, "alice": ALICE_YAML})

    with pytest.raises(PackError, match="bob"):
        load_pack(root)


def test_cast_member_without_name_raises_pack_error(tmp_path):
    root = write_pack(tmp_path / "bad",
                      cast={"gm": GM_YAML, "alice": {"archetype": "x"}, "bob": BOB_YAML})

    with pytest.raises(PackError, match="name"):
        load_pack(root)


def test_start_scene_not_among_scenes_raises_pack_error(tmp_path):
    root = write_pack(tmp_path / "bad",
                      campaign={**CAMPAIGN_YAML, "start_scene": "missing-scene"})

    with pytest.raises(PackError, match="missing-scene"):
        load_pack(root)


@pytest.mark.parametrize("missing_key", ["id", "next"])
def test_branch_missing_required_key_raises_pack_error(tmp_path, missing_key):
    branch = {"id": "success", "next": "victory"}
    del branch[missing_key]
    scene = {"id": "opening", "beats": [], "branches": [branch]}
    root = write_pack(tmp_path / "bad", scenes=[scene, VICTORY_SCENE])

    with pytest.raises(PackError, match=missing_key):
        load_pack(root)


@pytest.mark.parametrize("field_name", ["beats", "branches"])
def test_non_list_scene_collection_raises_pack_error(tmp_path, field_name):
    scene = {"id": "opening", "beats": [], "branches": [], field_name: "not-a-list"}
    root = write_pack(tmp_path / "bad", scenes=[scene, VICTORY_SCENE])

    with pytest.raises(PackError, match=field_name):
        load_pack(root)


# ── null-valued YAML keys collapse to the documented default, not None ───────
# `params:` with nothing after it parses as None, not {}. Every optional
# collection has to survive that or the runtime blows up on a beat that merely
# declared a key and left it empty.
def test_null_beat_collections_default_to_empty(tmp_path):
    scene = {
        "id": "opening",
        "beats": [{"type": "action", "primitive": "roll_check", "params": None}],
        "branches": [{"id": "b", "next": "victory", "when": None}],
    }
    root = write_pack(tmp_path / "nulls", scenes=[scene, VICTORY_SCENE])

    loaded = load_pack(root).scenes["opening"]

    assert loaded.beats[0].params == {}
    assert loaded.branches[0].when == {}


def test_null_scene_collections_default_to_empty(tmp_path):
    scene = {"id": "opening", "beats": None, "branches": None}
    root = write_pack(tmp_path / "nulls", scenes=[scene, VICTORY_SCENE])

    loaded = load_pack(root).scenes["opening"]

    assert loaded.beats == []
    assert loaded.branches == []


def test_null_campaign_collections_default_to_empty(tmp_path):
    campaign = {**CAMPAIGN_YAML, "players": None, "primitives": None, "theme": None}
    root = write_pack(tmp_path / "nulls", campaign=campaign,
                      cast={"gm": GM_YAML})

    pack = load_pack(root)

    assert pack.player_ids == []
    assert pack.primitives == []
    assert pack.theme == {}


def test_player_ids_is_a_copy_not_the_parsed_list(pack_root):
    pack = load_pack(pack_root)

    pack.player_ids.append("mallory")

    assert load_pack(pack_root).player_ids == ["alice", "bob"]
