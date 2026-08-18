"""Tests for app/campaign/batch_generate.py — offline batch content generation.

This is orchestration around the already-tested `LLMImproviser.generate_scene`
(see test_campaign_improviser.py), so these tests cover only the script's own
branching logic: take-number resumption, retry-then-skip on empty generation,
and the shape of what gets written to disk. No test here touches the network
or `build_llm_client` — every LLM is a fake, matching the convention in
test_campaign_improviser.py.
"""
import json
from pathlib import Path

import pytest
import yaml

from campaign.batch_generate import (
    _append_manifest,
    _generate_take,
    _next_take_number,
    _write_take,
)
from campaign.improviser import LLMImproviser
from campaign.pack import CampaignPack, CastMember, Scene


# ── fakes and fixtures ───────────────────────────────────────────────────────
class FakeLLM:
    """Records every call and returns canned replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies) or ["helen: A fresh line."]
        self.calls = []

    def complete(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0]


class ExplodingLLM:
    def __init__(self, exc=None):
        self.exc = exc or RuntimeError("model is on fire")
        self.calls = 0

    def complete(self, system_prompt, messages):
        self.calls += 1
        raise self.exc


def make_pack(scenes=()):
    cast = {
        "gm": CastMember(id="gm", name="The Chronicler", role="gm", archetype="narrator"),
        "helen": CastMember(id="helen", name="Helen", role="player", archetype="wizard"),
    }
    return CampaignPack(
        name="testpack", title="Test", genre="fantasy", start_scene="opening",
        gm_id="gm", player_ids=["helen"], primitives=[],
        theme={}, cast=cast, scenes={s.id: s for s in scenes},
        root=Path("/nonexistent"), lore_dir=None, lore={},
    )


def ambient_scene(prompt="Rain has stopped the party. Six short lines."):
    return Scene(id="camp-fire", title="A Fire, Waiting", ambient=True, prompt=prompt)


# ── _next_take_number ────────────────────────────────────────────────────────
def test_next_take_number_empty_dir_returns_1(tmp_path):
    assert _next_take_number(tmp_path) == 1


def test_next_take_number_resumes_after_existing_takes(tmp_path):
    (tmp_path / "001.yaml").write_text("id: x\n")
    (tmp_path / "002.yaml").write_text("id: x\n")
    (tmp_path / "004.yaml").write_text("id: x\n")  # a gap is fine; resume from the max
    assert _next_take_number(tmp_path) == 5


def test_next_take_number_ignores_non_numeric_files(tmp_path):
    (tmp_path / "001.yaml").write_text("id: x\n")
    (tmp_path / "manifest.jsonl").write_text("{}\n")
    (tmp_path / "notes.yaml").write_text("id: x\n")
    assert _next_take_number(tmp_path) == 2


# ── _generate_take ───────────────────────────────────────────────────────────
def test_generate_take_returns_beats_as_dicts():
    scene = ambient_scene()
    pack = make_pack([scene])
    llm = FakeLLM("helen: We should run.\ngm: The fire crackles.")
    improviser = LLMImproviser(pack, llm=llm)

    beats = _generate_take(improviser, scene, take=1, max_attempts=2)

    assert len(beats) == 2
    assert all(set(b) == {"kind", "speaker", "text"} for b in beats)
    assert beats[0]["speaker"] == "helen"
    assert beats[0]["text"] == "We should run."


def test_generate_take_retries_then_returns_empty_on_persistent_failure():
    scene = ambient_scene()
    pack = make_pack([scene])
    llm = ExplodingLLM()
    improviser = LLMImproviser(pack, llm=llm)

    beats = _generate_take(improviser, scene, take=1, max_attempts=3)

    assert beats == []
    assert llm.calls == 3  # one call per attempt, never raised out


def test_generate_take_succeeds_on_second_attempt():
    scene = ambient_scene()
    pack = make_pack([scene])
    # First reply is blank -> generate_scene returns [] -> retry. Second succeeds.
    llm = FakeLLM("   ", "helen: Good line.")
    improviser = LLMImproviser(pack, llm=llm)

    beats = _generate_take(improviser, scene, take=1, max_attempts=2)

    assert len(beats) == 1
    assert beats[0]["text"] == "Good line."


def test_generate_take_resets_transcript_between_attempts():
    """Each take is a standalone airing — no leakage from a prior observe() call."""
    scene = ambient_scene()
    pack = make_pack([scene])
    llm = FakeLLM("helen: Fresh every time.")
    improviser = LLMImproviser(pack, llm=llm)
    improviser.observe("helen", "leftover line from a previous take")

    _generate_take(improviser, scene, take=1, max_attempts=1)

    assert improviser.recent == []


# ── disk writes ──────────────────────────────────────────────────────────────
def test_write_take_round_trips_through_yaml(tmp_path):
    beats = [{"kind": "dialogue", "speaker": "helen", "text": "We should run."}]
    path = tmp_path / "001.yaml"

    _write_take(path, "camp-fire", 1, "hermes3:70b", "2026-08-17T00:00:00+00:00", beats)

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["id"] == "camp-fire"
    assert loaded["take"] == 1
    assert loaded["model"] == "hermes3:70b"
    assert loaded["beats"] == beats


def test_append_manifest_shape(tmp_path):
    manifest_path = tmp_path / "manifest.jsonl"
    entry = {
        "scene_id": "camp-fire",
        "take": 1,
        "word_count": 42,
        "beat_count": 6,
        "model": "hermes3:70b",
        "timestamp": "2026-08-17T00:00:00+00:00",
    }

    _append_manifest(manifest_path, entry)
    _append_manifest(manifest_path, {**entry, "take": 2})

    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed == entry
