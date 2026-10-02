"""Tests for replay_pane's resumable-prep wiring (open_prep_checkpoint /
finish_prep_checkpoint / prepare_voice checkpoint pass-through) — see
docs/voice_prep_checkpoint.md. Episode store, narration store, and voice prep
are all stubbed; no DB, LLM or TTS."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import replay_pane  # noqa: E402
from replay_pane import (  # noqa: E402
    finish_prep_checkpoint, open_prep_checkpoint, perform_request, prepare_voice,
)


@pytest.fixture
def library(monkeypatch):
    scripts = {"ep1": {"source": "ep1", "events": [{"type": "assistant_text", "text": "hello"}]}}
    monkeypatch.setattr(replay_pane.episode_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane.episode_store, "load_episode", lambda name: scripts.get(name))
    monkeypatch.setattr(replay_pane.episode_store, "list_episodes", lambda: sorted(scripts))
    return scripts


class SpyCheckpoint:
    def __init__(self, restored=0, saved=0):
        self.restored, self.saved = restored, saved
        self.events = []

    def clear(self):
        self.events.append("clear")

    def close(self):
        self.events.append("close")


def test_open_prep_checkpoint_none_without_voice(monkeypatch):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    assert open_prep_checkpoint("ep1", None, "GM", 1.0) is None
    assert open_prep_checkpoint("ep1", {"voice": {}}, "GM", 1.0) is None


def test_open_prep_checkpoint_passes_worker_id(monkeypatch):
    seen = {}

    def fake_build(episode, worker_name, config, speed=1.0, worker_id=None):
        seen.update(episode=episode, worker_name=worker_name, speed=speed, worker_id=worker_id)
        return "cp"
    monkeypatch.setattr(replay_pane.voice_prep_checkpoint, "build_checkpoint", fake_build)
    config = {"voice": {"provider": "piper"}, "message_bus": {"worker_id": "roundtable"}}
    assert open_prep_checkpoint("ep1", config, "GM", 1.5) == "cp"
    assert seen == {"episode": "ep1", "worker_name": "GM", "speed": 1.5,
                    "worker_id": "roundtable"}


@pytest.mark.parametrize("persisted,expected", [
    ("airing-id", ["clear", "close"]),
    (None, ["close"]),   # save failed: keep rows so the next attempt skips to airing
    (False, ["close"]),
])
def test_finish_prep_checkpoint_clears_only_after_persist(persisted, expected):
    cp = SpyCheckpoint()
    finish_prep_checkpoint(cp, persisted)
    assert cp.events == expected


def test_finish_prep_checkpoint_none_is_noop():
    finish_prep_checkpoint(None, "x")


def test_prepare_voice_passes_checkpoint_and_reports_resume(monkeypatch, capsys):
    seen = {}

    def fake_prepare(script, config, workdir, **kwargs):
        seen.update(kwargs)
        return [{"audio": object()}, {"audio": None}]
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", fake_prepare)
    cp = SpyCheckpoint(restored=1, saved=1)
    show = prepare_voice({"events": []}, {"voice": {"provider": "piper"}}, "/tmp", "GM", 1.0,
                         checkpoint=cp)
    assert len(show) == 2 and seen["checkpoint"] is cp
    assert "resumed prep: 1 scenes restored" in capsys.readouterr().out


def test_prepare_voice_without_checkpoint_keeps_old_call_shape(monkeypatch):
    seen = {}

    def fake_prepare(script, config, workdir, **kwargs):
        seen.update(kwargs)
        return []
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", fake_prepare)
    prepare_voice({"events": []}, {"voice": {"provider": "piper"}}, "/tmp", "GM", 1.0)
    assert "checkpoint" not in seen


def test_solo_request_clears_checkpoint_after_successful_persist(library, monkeypatch):
    cp = SpyCheckpoint()
    monkeypatch.setattr(replay_pane, "open_prep_checkpoint", lambda *a, **k: cp)
    monkeypatch.setattr(replay_pane, "prepare_voiced_show",
                        lambda *a, **k: [{"events": [], "narration": "x", "audio": None}])
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **k: "mid")
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **k: "mid")
    assert perform_request({"episode": "ep1", "speed": 0}, "GM", None,
                           config={"voice": {"provider": "piper"}})
    assert cp.events == ["clear", "close"]


def test_solo_request_keeps_checkpoint_when_persist_fails(library, monkeypatch):
    cp = SpyCheckpoint()
    monkeypatch.setattr(replay_pane, "open_prep_checkpoint", lambda *a, **k: cp)
    monkeypatch.setattr(replay_pane, "prepare_voiced_show",
                        lambda *a, **k: [{"events": [], "narration": "x", "audio": None}])
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **k: None)
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **k: None)
    assert perform_request({"episode": "ep1", "speed": 0}, "GM", None,
                           config={"voice": {"provider": "piper"}})
    assert cp.events == ["close"]


def test_solo_request_closes_checkpoint_even_if_publish_raises(library, monkeypatch):
    cp = SpyCheckpoint()
    monkeypatch.setattr(replay_pane, "open_prep_checkpoint", lambda *a, **k: cp)
    monkeypatch.setattr(replay_pane, "prepare_voiced_show",
                        lambda *a, **k: [{"events": [], "narration": "x", "audio": None}])

    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(replay_pane, "publish_narration", boom)
    with pytest.raises(RuntimeError):
        perform_request({"episode": "ep1", "speed": 0}, "GM", None,
                        config={"voice": {"provider": "piper"}})
    assert cp.events == ["close"]


def test_reuse_hit_never_opens_checkpoint(library, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("no fresh prep on a reuse hit")
    monkeypatch.setattr(replay_pane, "open_prep_checkpoint", explode)
    monkeypatch.setattr(replay_pane, "load_reused_show",
                        lambda *a, **k: [{"events": [], "narration": "x", "audio": None}])
    assert perform_request({"episode": "ep1", "speed": 0, "narration": "reuse"}, "GM", None,
                           config={"voice": {"provider": "piper"}})
