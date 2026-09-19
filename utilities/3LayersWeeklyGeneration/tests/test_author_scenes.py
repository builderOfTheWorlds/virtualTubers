"""Tests for author_scenes.py — the LLM-driven scene authoring path.

The tests do NOT hit a real LLM; they use a FakeClient that returns
canned YAML, so the tests exercise the PARSE + FILTER + VALIDATE path
(which is what we need to check) deterministically.
"""
import pathlib
import sys
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
for path in (REPO / "app", REPO / "utilities" / "3LayersWeeklyGeneration" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import author_scenes as as_  # noqa: E402
from source_adapter import SourceNote  # noqa: E402


def _base_pack(base: pathlib.Path):
    """Build a minimal base pack with a known cast and lore set."""
    (base / "cast").mkdir(parents=True)
    (base / "lore").mkdir(parents=True)
    (base / "scenes").mkdir(parents=True)
    # NB: `players:` is the source of truth for `pack.cast` (see
    # app/campaign/pack.py:210-214). Register alice+bob there so the
    # local filter and the gate agree on the accepted speaker set.
    (base / "campaign.yaml").write_text(
        "name: fake\ntitle: T\ngenre: fantasy\n"
        "start_scene: open\n"
        "gm: gm\n"
        "players:\n"
        "  - alice\n"
        "  - bob\n"
        "primitives:\n")
    for stem in ("gm", "alice", "bob"):
        (base / "cast" / f"{stem}.yaml").write_text(f"name: {stem}\nrole: p\n")
    for stem in ("alpha", "beta"):
        (base / "lore" / f"{stem}.md").write_text(f"Lore: {stem}.\n")
    (base / "scenes" / "01-open.yaml").write_text(
        "id: open\ntitle: Open\nenter_narration: Start.\n"
        "beats:\n  - type: narration\n    speaker: gm\n    text: Hello.\n")


def _note(text="A note body."):
    return SourceNote(
        id="note-1",
        title="A note",
        text=text,
        kind="plot",
        rel_path="Plots/A.md",
        hash="h" * 64,
    )


class FakeClient:
    """Returns a list of canned replies in order; when exhausted, repeats
    the last one forever (useful for testing the retry path)."""
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, messages, **kwargs) -> str:
        self.calls += 1
        if not self.replies:
            raise AssertionError("FakeClient exhausted; add more canned replies")
        return self.replies.pop(0)

    def close(self):
        pass


def test_author_spine_returns_scene_dict_with_provenance():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        good_yaml = (
            "id: test-scene\n"
            "title: Test\n"
            "enter_narration: Start.\n"
            "beats:\n"
            "  - type: dialogue\n"
            "    speaker: alice\n"
            "    text:\n"
            "      - Line A\n"
            "      - Line B\n"
            "lore: [alpha]\n"
        )
        client = FakeClient([f"```yaml\n{good_yaml}\n```"])
        scene = as_.author_spine(client, _note(), base_pack_dir=base,
                                  run_id="r", batch="b", model="m", cast_ids=["gm", "alice"])
        assert scene["id"] == "test-scene"
        assert scene["source"]["run_id"] == "r"
        assert scene["source"]["authored"] == "generated"
        assert scene["lore"] == ["alpha"]
        assert len(scene["beats"]) == 1
        assert scene["beats"][0]["text"] == ["Line A", "Line B"]
        assert client.calls == 1


def test_author_spine_filters_unknown_lore_stems_locally():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        # Model invents two stems: 'alpha' (known) and 'ghost-note' (unknown).
        # The local filter should keep only 'alpha'.
        good_yaml = (
            "id: test-scene\n"
            "title: T\n"
            "enter_narration: Hi.\n"
            "beats:\n"
            "  - type: narration\n"
            "    speaker: gm\n"
            "    text: Line.\n"
            "lore: [alpha, ghost-note]\n"
        )
        client = FakeClient(["```yaml\n" + good_yaml + "\n```"])
        scene = as_.author_spine(client, _note(), base_pack_dir=base,
                                  run_id="r", batch="b", model="m", cast_ids=["gm"])
        assert scene["lore"] == ["alpha"]  # ghost-note filtered out
        assert scene["id"] == "test-scene"


def test_author_spine_filters_unknown_speakers_locally():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        # Model invents beats: one with alice (known), one with ghost (unknown).
        # The local filter should keep only the alice beat.
        good_yaml = (
            "id: test-scene\n"
            "title: T\n"
            "enter_narration: Hi.\n"
            "beats:\n"
            "  - type: dialogue\n"
            "    speaker: alice\n"
            "    text: I'm here.\n"
            "  - type: dialogue\n"
            "    speaker: ghost\n"
            "    text: Boo.\n"
            "lore: []\n"
        )
        client = FakeClient(["```yaml\n" + good_yaml + "\n```"])
        scene = as_.author_spine(client, _note(), base_pack_dir=base,
                                  run_id="r", batch="b", model="m", cast_ids=["alice"])
        # ghost beat dropped; alice beat kept.
        assert len(scene["beats"]) == 1
        assert scene["beats"][0]["speaker"] == "alice"


def test_author_spine_filters_unknown_primitives_locally():
    """A model can invent `type: action` beats with primitives the campaign
    never enabled (investigate/interact were seen live on the Ashiorid run).
    validator.py:218-224 rejects those; the local filter must drop them
    BEFORE the gate, so the gate is never the first place it's discovered."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)  # primitives: (empty) — nothing enabled
        good_yaml = (
            "id: test-scene\n"
            "title: T\n"
            "enter_narration: Hi.\n"
            "beats:\n"
            "  - type: dialogue\n"
            "    speaker: alice\n"
            "    text:\n"
            "      - Line A\n"
            "  - type: action\n"
            "    speaker: bob\n"
            "    primitive: investigate\n"
            "  - type: action\n"
            "    speaker: bob\n"
            "    primitive: cast_spell\n"
            "lore: []\n"
        )
        client = FakeClient(["```yaml\n" + good_yaml + "\n```"])
        scene = as_.author_spine(client, _note(), base_pack_dir=base,
                                 run_id="r", batch="b", model="m", cast_ids=["alice"])
        # both action beats dropped (no primitive is enabled in this base);
        # the dialogue beat survives.
        assert len(scene["beats"]) == 1
        assert scene["beats"][0]["type"] == "dialogue"


def test_author_spine_keeps_action_beats_with_enabled_primitives():
    """The drop must be keyed on the campaign's ENABLED primitives, not a
    blanket ban on action beats — an enabled primitive stays."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        (base / "campaign.yaml").write_text(
            "name: fake\ntitle: T\ngenre: fantasy\nstart_scene: open\n"
            "gm: gm\nplayers:\n  - alice\n  - bob\n"
            "primitives:\n  - search\n  - roll_check\n")
        good_yaml = (
            "id: test-scene\n"
            "title: T\n"
            "enter_narration: Hi.\n"
            "beats:\n"
            "  - type: action\n"
            "    speaker: alice\n"
            "    primitive: search\n"
            "  - type: action\n"
            "    speaker: bob\n"
            "    primitive: investigate\n"
            "lore: []\n"
        )
        client = FakeClient(["```yaml\n" + good_yaml + "\n```"])
        scene = as_.author_spine(client, _note(), base_pack_dir=base,
                                 run_id="r", batch="b", model="m", cast_ids=["alice"])
        kept = [b for b in scene["beats"] if b.get("type") == "action"]
        assert len(kept) == 1, kept
        assert kept[0]["primitive"] == "search"


def test_author_spine_retries_on_bad_yaml():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        good_yaml = (
            "id: test-scene\n"
            "title: T\n"
            "enter_narration: Hi.\n"
            "beats:\n"
            "  - type: narration\n"
            "    speaker: gm\n"
            "    text: Line.\n"
            "lore: []\n"
        )
        # First reply is unparseable, second is good.
        client = FakeClient(["no yaml here, just prose", "```yaml\n" + good_yaml + "\n```"])
        scene = as_.author_spine(client, _note(), base_pack_dir=base,
                                  run_id="r", batch="b", model="m", cast_ids=["gm"])
        assert scene["id"] == "test-scene"
        assert client.calls == 2  # one failed, one succeeded


def test_author_spine_gives_up_after_max_retries():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        # Both replies are unparseable.
        client = FakeClient(["bad", "still bad"])
        with pytest.raises(as_.AuthoringError):
            as_.author_spine(client, _note(), base_pack_dir=base,
                              run_id="r", batch="b", model="m", cast_ids=["gm"],
                              max_retries=2)
        assert client.calls == 2


def test_author_ambient_strips_beats_and_default_next():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        # Model tries to include forbidden fields for an ambient scene.
        # The author should pop them.
        yaml = (
            "id: a-amb\n"
            "title: T\n"
            "ambient: true\n"
            "prompt: A quiet prompt.\n"
            "beats:\n"
            "  - type: narration\n"
            "    speaker: gm\n"
            "    text: Should-not-exist.\n"
            "default_next: nowhere\n"
            "lore: [alpha]\n"
        )
        client = FakeClient(["```yaml\n" + yaml + "\n```"])
        scene = as_.author_ambient(client, _note(), base_pack_dir=base,
                                    run_id="r", batch="b", model="m")
        assert scene["ambient"] is True
        assert "beats" not in scene
        assert "default_next" not in scene
        assert scene["prompt"].strip().startswith("A quiet")
        assert scene["lore"] == ["alpha"]


def test_author_ambient_filters_unknown_lore_locally():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        base.mkdir()
        _base_pack(base)
        yaml = (
            "id: a-amb\n"
            "title: T\n"
            "ambient: true\n"
            "prompt: Quiet.\n"
            "lore: [alpha, ghost, beta]\n"
        )
        client = FakeClient(["```yaml\n" + yaml + "\n```"])
        scene = as_.author_ambient(client, _note(), base_pack_dir=base,
                                    run_id="r", batch="b", model="m")
        # Only known stems alpha + beta survive.
        assert set(scene["lore"]) == {"alpha", "beta"}
