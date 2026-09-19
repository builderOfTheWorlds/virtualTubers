"""Tests for pack_gate.py — the only path between a staged scene and a tracked
pack. Uses a minimal fake base pack built in a temp dir so the test does not
depend on the real ashiorid_1 pack."""
import pathlib
import sys
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
for path in (REPO / "app", REPO / "utilities" / "3LayersWeeklyGeneration" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import scene_writer as sw  # noqa: E402
from pack_gate import check_stage, promote, GateError  # noqa: E402


def _write_base_pack(base: pathlib.Path):
    """Minimal pack: campaign.yaml + gm + 1 spine scene + 1 lore note."""
    (base / "cast").mkdir(parents=True)
    (base / "lore").mkdir(parents=True)
    (base / "scenes").mkdir(parents=True)
    # NB: `players:` is the source of truth for `pack.cast` (see
    # app/campaign/pack.py:210-214) — the ambient tests don't speak as
    # anyone, but keep the pack well-formed anyway.
    (base / "campaign.yaml").write_text(
        "name: fake\n"
        "title: Fake\n"
        "genre: fantasy\n"
        "start_scene: open\n"
        "gm: gm\n"
        "players:\n"
        "primitives:\n")
    (base / "cast" / "gm.yaml").write_text("name: GM\nrole: gm\n")
    (base / "lore" / "alpha.md").write_text("A real lore note.\n")
    (base / "scenes" / "01-open.yaml").write_text(
        "id: open\n"
        "title: Open\n"
        "enter_narration: \"Start.\"\n"
        "beats:\n"
        "  - type: narration\n"
        "    speaker: gm\n"
        "    text: \"The story begins.\"\n")


def _good_ambient():
    return {
        "id": "a-filler",
        "title": "A filler",
        "ambient": True,
        "prompt": "The fire holds.",
        "lore": ["alpha"],
        "source": sw.provenance_block(
            run_id="r", batch="1.3", model="m",
            base_hash="h", version="a-filler@1"),
    }


def _bad_ambient_missing_lore():
    d = _good_ambient()
    d["id"] = "a-bad"
    d["lore"] = ["ghost-note"]  # not in lore/
    d["source"]["version"] = "a-bad@1"
    return d


def _bad_ambient_no_content():
    return {
        "id": "a-empty",
        "title": "Empty",
        "ambient": True,
        # no prompt, no beats
        "source": sw.provenance_block(run_id="r", batch="1.3", model="m",
                                       base_hash="h", version="a-empty@1"),
    }


def test_gate_passes_on_clean_staging():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _write_base_pack(base)
        staging = pathlib.Path(d) / "staging"
        staging.mkdir()
        sw.write_scene(staging, "a001-a-filler.yaml", _good_ambient())
        result = check_stage(base, staging)
        assert result.ok, "; ".join(result.errors)
        assert result.summary().startswith("PASS")


def test_gate_fails_on_unknown_lore_stem():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"; base.mkdir()
        _write_base_pack(base)
        staging = pathlib.Path(d) / "staging"; staging.mkdir()
        sw.write_scene(staging, "a001-a-bad.yaml", _bad_ambient_missing_lore())
        result = check_stage(base, staging)
        assert not result.ok
        assert any("ghost-note" in e for e in result.errors)


def test_gate_fails_on_empty_ambient():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"; base.mkdir()
        _write_base_pack(base)
        staging = pathlib.Path(d) / "staging"; staging.mkdir()
        sw.write_scene(staging, "a001-a-empty.yaml", _bad_ambient_no_content())
        result = check_stage(base, staging)
        assert not result.ok
        joined = " ".join(result.errors)
        assert "a-empty" in joined


def test_promote_refuses_when_gate_is_red():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"; base.mkdir()
        _write_base_pack(base)
        staging = pathlib.Path(d) / "staging"; staging.mkdir()
        sw.write_scene(staging, "a001-a-bad.yaml", _bad_ambient_missing_lore())
        with pytest.raises(GateError, match="gate failed"):
            promote(base, staging)
        # And the base pack must be untouched — no new scene in scenes/.
        scenes = list((base / "scenes").glob("*.yaml"))
        assert len(scenes) == 1
        assert scenes[0].name == "01-open.yaml"


def test_promote_writes_the_scene_when_gate_is_green():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"; base.mkdir()
        _write_base_pack(base)
        staging = pathlib.Path(d) / "staging"; staging.mkdir()
        sw.write_scene(staging, "a001-a-filler.yaml", _good_ambient())
        promoted = promote(base, staging, run_id="r-test")
        assert any(f.stem.endswith("a-filler") for f in (base / "scenes").iterdir())
        # Base pack now has TWO scenes.
        scenes = sorted(p.name for p in (base / "scenes").glob("*.yaml"))
        assert len(scenes) == 2
        # And the file carries the provenance block verbatim.
        import yaml
        target = next((base / "scenes").glob("*a-filler.yaml"))
        loaded = yaml.safe_load(target.read_text())
        assert loaded["id"] == "a-filler"
        assert loaded["source"]["run_id"] == "r"
        assert loaded["source"]["version"] == "a-filler@1"


def test_gate_result_falsy_and_ok():
    from pack_gate import GateResult
    ok = GateResult(True, [], ["w"])
    assert ok.ok and bool(ok)
    bad = GateResult(False, ["e1"], ["w"])
    assert not bad.ok and not bool(bad)
    assert "1" in bad.summary()


def test_gate_requires_campaign_yaml():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"; base.mkdir()
        staging = pathlib.Path(d) / "staging"; staging.mkdir()
        with pytest.raises(GateError, match="campaign.yaml"):
            check_stage(base, staging)
