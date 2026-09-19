"""Tests for spine_chain.py — Phase 2.4.

The pure half (link_spines, build_previous_scene_context,
_base_pack_open_spines) runs without an LLM. The LLM half is exercised
with the same FakeClient pattern as test_author_scenes.py: a canned
YAML reply per call, in chain order, so we can assert the sequential
context-carrying behaviour deterministically.
"""
import pathlib
import sys
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
for path in (REPO / "app", REPO / "utilities" / "3LayersWeeklyGeneration" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import yaml

import spine_chain as sc          # noqa: E402
import source_adapter as sa      # noqa: E402
import scene_writer as sw        # noqa: E402


def _base_pack(base: pathlib.Path):
    (base / "cast").mkdir(parents=True)
    (base / "lore").mkdir(parents=True)
    (base / "scenes").mkdir(parents=True)
    # NB: `players:` is the source of truth for `pack.cast` (see
    # app/campaign/pack.py:210-214). A cast/*.yaml file that is not in
    # `players:` is NOT a valid speaker at gate time. The fixture lists
    # alice in players so the pack is well-formed.
    (base / "campaign.yaml").write_text(
        "name: fake\n"
        "title: T\n"
        "genre: fantasy\n"
        "start_scene: seed\n"
        "gm: gm\n"
        "players:\n"
        "  - alice\n"
        "primitives:\n")
    (base / "cast" / "gm.yaml").write_text("name: GM\nrole: gm\n")
    (base / "cast" / "alice.yaml").write_text("name: A\nrole: p\n")
    (base / "lore" / "alpha.md").write_text("Lore.\n")
    # Seed spine: has no default_next -> is the ONLY open spine candidate.
    (base / "scenes" / "01-seed.yaml").write_text(
        "id: seed\ntitle: Seed\nenter_narration: Start.\n"
        "beats:\n  - type: narration\n    speaker: gm\n    text: Hello.\n"
        "\n")


def _note(i: int) -> sa.SourceNote:
    return sa.SourceNote(
        id=f"note-{i}", title=f"Note {i}", text=f"body {i}", kind="plot",
        rel_path=f"Plots/{i}.md", hash="h" * 64)


# ── pure half ────────────────────────────────────────────────────────

def test_link_spines_wires_default_next_across_chain():
    s = [
        {"id": "a"}, {"id": "b"}, {"id": "c"},
    ]
    sc.link_spines(s)
    assert s[0]["default_next"] == "b"
    assert s[1]["default_next"] == "c"
    assert s[2]["default_next"] is None


def test_link_spines_single_scene_has_no_default_next():
    s = [{"id": "only"}]
    sc.link_spines(s)
    assert s[0]["default_next"] is None


def test_build_previous_scene_context_includes_committed_output():
    prev = {
        "id": "prev-beat",
        "title": "Prev",
        "enter_narration": "A line.",
        "beats": [{"type": "dialogue", "speaker": "alice",
                    "text": ["Hi.", "Hey."]}],
        "source": {"run_id": "secret-run", "batch": "x",
                    "model": "m", "base_hash": "h", "version": "prev-beat@1"},
    }
    ctx = sc.build_previous_scene_context(prev)
    assert "prev-beat" in ctx                 # committed id is visible
    assert "alice" in ctx                     # the speaker is visible
    assert "secret-run" not in ctx            # provenance must not leak
    assert "PREVIOUS SPINE SCENE" in ctx


def test_base_pack_insertion_points_find_open_ends():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        # The seed has no default_next -> an open insertion point.
        assert sc._base_pack_insertion_points(base) == [
            {"type": "open", "scene": "seed"}]
        # Now wire a spine AFTER it: seed gets a default_next -> no longer
        # open; the new last scene is open.
        (base / "scenes" / "01-seed.yaml").write_text(
            "id: seed\ntitle: Seed\nenter_narration: Start.\n"
            "default_next: next\n"
            "beats:\n  - type: narration\n    speaker: gm\n    text: Hello.\n")
        (base / "scenes" / "02-next.yaml").write_text(
            "id: next\ntitle: Next\nenter_narration: N.\n"
            "beats:\n  - type: narration\n    speaker: gm\n    text: N.\n")
        assert sc._base_pack_insertion_points(base) == [
            {"type": "open", "scene": "next"}]


def test_base_pack_insertion_points_find_self_loop_spines():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        # Make the seed a self-loop spine (the show's looping premise):
        # default_next pointing at itself. This is a legal, deliberate
        # end — and a chain must be able to splice in.
        (base / "scenes" / "01-seed.yaml").write_text(
            "id: seed\ntitle: Seed\nenter_narration: Start.\n"
            "default_next: seed\n"
            "beats:\n  - type: narration\n    speaker: gm\n    text: Hello.\n")
        points = sc._base_pack_insertion_points(base)
        assert points == [{"type": "loop", "scene": "seed"}]


# ── LLM half (fake client) ───────────────────────────────────────────

class FakeClient:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []  # (system, user)

    def complete(self, system, messages, **kw):
        user = messages[0]["content"]
        self.calls.append((system, user))
        if not self.replies:
            raise AssertionError("FakeClient exhausted")
        return self.replies.pop(0)

    def close(self): ...


def _good_scene_yaml(i: int) -> str:
    return (
        f"id: beat-{i}\n"
        "title: Beat\n"
        "enter_narration: A line.\n"
        "beats:\n"
        "  - type: dialogue\n"
        "    speaker: alice\n"
        "    text:\n"
        "      - Hi.\n"
        "      - Hey.\n"
        "lore: []\n"
    )


def test_author_spine_chain_authors_sequentially_and_links():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        staging = pathlib.Path(d) / "staging"

        client = FakeClient([
            "```yaml\n" + _good_scene_yaml(1) + "\n```",
            "```yaml\n" + _good_scene_yaml(2) + "\n```",
        ])
        res = sc.author_spine_chain(
            client, [_note(1), _note(2)],
            base_pack_dir=base, staging_dir=staging,
            run_id="r", batch="b", model="m", cast_ids=["gm", "alice"],
        )
        assert [s["id"] for s in res["scenes"]] == ["beat-1", "beat-2"]
        assert res["first_id"] == "beat-1"
        assert res["last_id"] == "beat-2"
        # Chained.
        assert res["scenes"][0]["default_next"] == "beat-2"
        assert res["scenes"][1]["default_next"] is None
        # The base pack's single open spine is proposed as the
        # insertion point.
        assert res["base_candidates"] == [{"type": "open", "scene": "seed"}]
        assert res["patch"]["set"] == {
            "scene": "seed", "default_next": "beat-1"}
        assert res["patch"]["insertion_type"] == "open"
        assert res["patch"]["loop_closure"] is None
        # Staged files: one per scene, in order.
        assert [pathlib.Path(p).name for p in res["staged_files"]] == [
            "001-beat-1.yaml", "002-beat-2.yaml"]
        assert len(client.calls) == 2
        # CRITICAL: the second call's SYSTEM prompt contained the first
        # scene's committed output — that is the sequential-context rule
        # (§2.6: author scene N with scene N-1's committed output in context).
        assert "beat-1" in client.calls[1][0]
        assert "PREVIOUS SPINE SCENE" in client.calls[1][0]
        # And the FIRST call did NOT (no previous scene yet).
        assert "PREVIOUS SPINE SCENE" not in client.calls[0][0]


def test_author_spine_chain_requires_notes():
    with pytest.raises(ValueError):
        sc.author_spine_chain(FakeClient([]), [],
                              base_pack_dir="x", staging_dir="y",
                              run_id="r", batch="b", model="m",
                              cast_ids=["gm"])


def test_promote_chained_refuses_red_gate():
    import scene_writer as sw
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        staging = pathlib.Path(d) / "staging"
        staging.mkdir()
        # Write a scene that has a beat speaking as a NON-CAST member —
        # that is a red gate (unknown speaker).
        bad = {
            "id": "red-scene",
            "title": "R",
            "enter_narration": "hi",
            "beats": [{"type": "dialogue", "speaker": "ghost", "text": ["boo"]}],
            "default_next": None,
            "source": sw.provenance_block(run_id="r", batch="b",
                                           model="m", base_hash="h",
                                           version="red-scene@1"),
        }
        sw.write_scene(staging, "001-red-scene.yaml", bad)
        result = {"scenes": [bad], "first_id": "red-scene",
                  "last_id": "red-scene",
                  "base_candidates": ["seed"],
                  "patch": {"set": {"scene": "seed",
                                     "default_next": "red-scene"},
                            "alternatives": ["seed"]}}
        with pytest.raises(sc.GateError, match="gate failed"):
            sc.promote_chained(base, staging, result, run_id="r")
        # Base pack's scenes/ must be untouched.
        assert len(list((base / "scenes").glob("*.yaml"))) == 1


def _chain_result(first_id, last_id, insert={"type": "open", "scene": "seed"}):
    patch = {"insert_at": insert["scene"],
             "insertion_type": insert["type"],
             "set": {"scene": insert["scene"], "default_next": first_id},
             "loop_closure": None,
             "alternatives": [insert]}
    if insert["type"] == "loop":
        patch["loop_closure"] = {"scene": last_id,
                                 "default_next": insert["scene"]}
    s1 = {
        "id": first_id, "title": "B1", "enter_narration": "hi",
        "default_next": last_id, "beats": [],
        "source": sw.provenance_block(run_id="r", batch="b", version=first_id + "@1",
                                       model="m", base_hash="h"),
    }
    s2 = {
        "id": last_id, "title": "B2", "enter_narration": "yo",
        "default_next": None, "beats": [],
        "source": sw.provenance_block(run_id="r", batch="b", version=last_id + "@1",
                                       model="m", base_hash="h"),
    }
    return {"scenes": [s1, s2], "first_id": first_id, "last_id": last_id,
            "base_candidates": [insert], "patch": patch,
            "staged_files": [], "run_id": "r"}


def test_promote_chained_into_a_loop_spine_restores_the_loop_and_validates():
    """The HP test pack case: seed spine self-loops (default_next: it).
    Splice in [beat-1, beat-2] and restore the loop (beat-2 -> seed).
    After the promoted edit the pack must still validate — the loop is
    intact, nothing unreachable, nothing broken."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        import campaign.pack as cp
        import campaign.validator as cv
        _base_pack(base)
        # Turn the seed into a self-loop spine.
        (base / "scenes" / "01-seed.yaml").write_text(
            "id: seed\ntitle: Seed\nenter_narration: Start.\n"
            "default_next: seed\n"
            "beats:\n  - type: narration\n    speaker: gm\n    text: Hello.\n")
        # Sanity: the looped pack validates today.
        assert cv.validate_pack(cp.load_pack(base)).ok

        staging = pathlib.Path(d) / "staging"
        staging.mkdir()
        result = _chain_result("beat-1", "beat-2",
                               insert={"type": "loop", "scene": "seed"})
        # Stage two scenes with matching ids.
        sw.write_scene(staging, "001-beat-1.yaml",
                       dict(result["scenes"][0]))
        sw.write_scene(staging, "002-beat-2.yaml",
                       dict(result["scenes"][1]))
        written = sc.promote_chained(base, staging, result, run_id="r")
        # Apply the two proposal edits (this is what a human would do):
        # seed: default_next seed -> beat-1. beat-2: None -> seed.
        seed_file = next((base / "scenes").glob("01-seed.yaml"))
        seed = yaml.safe_load(seed_file.read_text())
        seed["default_next"] = "beat-1"
        seed_file.write_text(yaml.safe_dump(seed, sort_keys=False))
        last = next((base / "scenes").glob("002-beat-2.yaml"))
        last_data = yaml.safe_load(last.read_text())
        last_data["default_next"] = "seed"
        last.write_text(yaml.safe_dump(last_data, sort_keys=False))

        # The spliced pack must validate: loop intact, all reachable.
        report = cv.validate_pack(cp.load_pack(base))
        assert report.ok, " ".join(report.errors + report.warnings)
        # And the loop closure file was proposed.
        prop = (staging / "campaign_patch.proposal.yaml").read_text()
        assert "loop" in prop
        assert "seed" in prop


def test_promote_chained_writes_scenes_and_proposal_on_green():
    import scene_writer as sw
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        staging = pathlib.Path(d) / "staging"
        staging.mkdir()
        # Two chained green scenes.
        s1 = {
            "id": "beat-1", "title": "B1", "enter_narration": "hi",
            "default_next": "beat-2",
            "beats": [{"type": "dialogue", "speaker": "alice",
                        "text": ["hi", "hey"]}],
            "source": sw.provenance_block(run_id="r", batch="b",
                                           model="m", base_hash="h",
                                           version="beat-1@1"),
        }
        s2 = {
            "id": "beat-2", "title": "B2", "enter_narration": "yo",
            "default_next": None,
            "beats": [{"type": "narration", "speaker": "gm",
                        "text": ["on we go"]}],
            "source": sw.provenance_block(run_id="r", batch="b",
                                           model="m", base_hash="h",
                                           version="beat-2@1"),
        }
        sw.write_scene(staging, "001-beat-1.yaml", s1)
        sw.write_scene(staging, "002-beat-2.yaml", s2)
        insert = {"type": "open", "scene": "seed"}
        result = {
            "scenes": [s1, s2], "first_id": "beat-1", "last_id": "beat-2",
            "base_candidates": [insert],
            "patch": {
                "insert_at": "seed", "insertion_type": "open",
                "set": {"scene": "seed", "default_next": "beat-1"},
                "loop_closure": None, "alternatives": [insert],
            },
            "staged_files": [], "run_id": "r",
        }
        written = sc.promote_chained(base, staging, result, run_id="r")
        scenes = sorted(p.name for p in (base / "scenes").glob("*.yaml"))
        # Seed + 2 promoted = 3.
        assert len(scenes) == 3
        assert any("beat-1" in n for n in scenes)
        assert any("beat-2" in n for n in scenes)
        # And the proposal file exists.
        prop = staging / "campaign_patch.proposal.yaml"
        assert prop.exists()
        text = prop.read_text()
        assert "seed" in text
        assert "beat-1" in text


# ── end ────────────────────────────────────────────────────────────────
