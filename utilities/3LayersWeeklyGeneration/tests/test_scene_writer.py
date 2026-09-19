"""Unit tests for scene_writer.py — pure serialization, no LLM, no network."""
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import scene_writer as sw  # noqa: E402


def test_slugify_is_stable_and_lowercase():
    assert sw.slugify("The Boy Who Lived!") == "the-boy-who-lived"
    # Apostrophe becomes an empty slot, so 's -> '-s- -> 's'.
    assert sw.slugify("Harry Potter And The Sorcerer's Stone") == "harry-potter-and-the-sorcerer-s-stone"


def test_slugify_rejects_non_slug_input():
    with pytest.raises(sw.SceneWriterError):
        sw.slugify("")


def test_hash_text_is_stable_and_differs():
    assert sw.hash_text("abc") == sw.hash_text("abc")
    assert sw.hash_text("abc") != sw.hash_text("abd")
    assert len(sw.hash_text("abc")) == 64


def test_fresh_run_id_format():
    import re
    assert re.match(r"ash_test_\d{8}_\d{6}$", sw.fresh_run_id("ash_test"))
    assert re.match(r"build_\d{8}_\d{6}$", sw.fresh_run_id())


def test_scene_dict_required_id():
    with pytest.raises(sw.SceneWriterError):
        sw.scene_dict_to_yaml({"title": "X"})


def test_scene_dict_invalid_id_rejected():
    with pytest.raises(sw.SceneWriterError):
        sw.scene_dict_to_yaml({"id": "Bad_ID!", "title": "x"})


def test_scene_yaml_roundtrips():
    scene = {
        "id": "camp-fire",
        "title": "A Fire, Waiting",
        "ambient": True,
        "prompt": "Rain has stopped the party.",
        "lore": ["the-event"],
        "source": sw.provenance_block(
            run_id="r1", batch="1.3", model="hermes3:70b",
            base_hash="h", version="camp-fire@1"),
    }
    text = sw.scene_dict_to_yaml(scene)
    parsed = sw.roundtrip_check(scene)
    assert parsed["id"] == "camp-fire"
    assert parsed["ambient"] is True
    assert parsed["prompt"].startswith("Rain")
    assert parsed["lore"] == ["the-event"]
    assert parsed["source"]["run_id"] == "r1"
    assert parsed["source"]["authored"] == "generated"


def test_scene_yaml_stable_across_runs():
    scene = {"id": "s1", "title": "T", "prompt": "p"}
    assert sw.scene_dict_to_yaml(scene) == sw.scene_dict_to_yaml(scene)


def test_next_scene_filename_allocates_next():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d)
        (p / "a001-foo.yaml").write_text("x: 1")
        (p / "a003-bar.yaml").write_text("x: 1")
        (p / "a009-baz.yaml").write_text("x: 1")
        assert sw.next_scene_filename(p, prefix="a") == "a002"
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d)
        (p / "a001-foo.yaml").write_text("x: 1")
        (p / "005-spine.yaml").write_text("x: 1")
        assert sw.next_scene_filename(p, prefix="") == "001"


def test_next_scene_filename_handles_nonexistent_dir():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "does-not-exist"
        assert sw.next_scene_filename(p, prefix="a") == "a001"


def test_write_scene_writes_a_file():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d)
        target = sw.write_scene(p, "a012-test.yaml",
                                {"id": "test", "title": "T", "prompt": "p"})
        assert pathlib.Path(target).exists()
        assert pathlib.Path(target).read_text().startswith("id: test")


def test_provenance_block_requires_all_fields():
    with pytest.raises(sw.SceneWriterError):
        sw.provenance_block(run_id="r", batch="b", model="m",
                             base_hash="", version="v")


def test_provenance_block_rejects_bad_authored():
    with pytest.raises(sw.SceneWriterError):
        sw.provenance_block(run_id="r", batch="b", model="m", base_hash="h",
                             version="v", authored="model")


def test_provenance_block_accepts_all_authored_states():
    for state in ("generated", "human", "human-edited"):
        block = sw.provenance_block(run_id="r", batch="b", model="m",
                                    base_hash="h", version="v", authored=state)
        assert block["authored"] == state
