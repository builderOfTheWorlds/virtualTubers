"""Tests for app/replay_pane.py and agent.py's replay_request handler —
the operator → agent → pane wiring for Rerun Theater.

Safety property under test: a bus payload can only ever select a
pre-built episode INSIDE the library — never a path outside it.
"""
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import agent  # noqa: E402
from agent import MESSAGE_HANDLERS, handle_replay_request  # noqa: E402
import replay_pane  # noqa: E402
from replay_pane import (  # noqa: E402
    list_episodes,
    load_reused_show,
    load_worker_config,
    perform_director_request,
    perform_follower_request,
    perform_request,
    persist_narration,
    publish_narration,
    read_request,
    resolve_episode,
    resolve_self_id,
)


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "replays"
    lib.mkdir()
    script = {"source": "ep1", "events": [{"type": "assistant_text", "text": "hello stream"}]}
    (lib / "ep1.json").write_text(json.dumps(script), encoding="utf-8")
    return lib


# ── resolve_episode: the traversal gate ──────────────────────────────────────
def test_resolve_episode_finds_with_and_without_extension(library):
    assert resolve_episode(library, "ep1") == library / "ep1.json"
    assert resolve_episode(library, "ep1.json") == library / "ep1.json"


@pytest.mark.parametrize("hostile", [
    "../../../etc/passwd",
    "..\\..\\secrets.json",
    "/etc/passwd",
    "c:\\Users\\dev\\.env",
])
def test_resolve_episode_never_escapes_library(library, hostile):
    resolved = resolve_episode(library, hostile)
    assert resolved is None or resolved.parent == library


def test_resolve_episode_missing_or_empty_returns_none(library):
    assert resolve_episode(library, "nope") is None
    assert resolve_episode(library, "") is None
    assert resolve_episode(library, None) is None


# ── read_request: consume-once, malformed-safe ───────────────────────────────
def test_read_request_consumes_file(tmp_path):
    req = tmp_path / "req.json"
    req.write_text(json.dumps({"episode": "ep1"}), encoding="utf-8")
    assert read_request(req) == {"episode": "ep1"}
    assert not req.exists()
    assert read_request(req) is None  # nothing pending


@pytest.mark.parametrize("content", ["not json {", "true", "[1,2]"])
def test_read_request_discards_malformed_without_raising(tmp_path, content):
    req = tmp_path / "req.json"
    req.write_text(content, encoding="utf-8")
    assert read_request(req) is None
    assert not req.exists()  # consumed, not wedged in a crash loop


# ── perform_request ──────────────────────────────────────────────────────────
def test_perform_request_plays_existing_episode(library, capsys):
    ok = perform_request({"episode": "ep1", "speed": 0}, library, "KODI-7", None,
                         default_speed=1.0)
    out = capsys.readouterr().out
    assert ok is True
    assert "hello stream" in out


def test_perform_request_unknown_episode_reports_false(library, capsys):
    ok = perform_request({"episode": "missing"}, library, "KODI-7", None)
    assert ok is False
    assert "not found" in capsys.readouterr().err


def test_perform_request_voiced_show_prepared_from_config(library, capsys, monkeypatch):
    prepared = {}

    def fake_prepare(script, config, workdir, **kwargs):
        prepared["config"] = config
        return [{"events": script["events"], "narration": "tonight's line", "audio": None}]

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", fake_prepare)
    config = {"voice": {"provider": "piper"}}
    ok = perform_request({"episode": "ep1", "speed": 0}, library, "KODI-7", None,
                         config=config)
    out = capsys.readouterr().out
    assert ok is True
    assert prepared["config"] is config
    assert "tonight's line" in out and "hello stream" in out


def test_perform_request_voice_false_skips_preparation(library, capsys, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("voice preparation should not run")

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", explode)
    ok = perform_request({"episode": "ep1", "speed": 0, "voice": False},
                         library, "KODI-7", None, config={"voice": {"provider": "piper"}})
    assert ok is True
    assert "hello stream" in capsys.readouterr().out


def test_perform_request_voice_failure_still_airs_silent(library, capsys, monkeypatch):
    def broken_prepare(*args, **kwargs):
        raise RuntimeError("ollama exploded")

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", broken_prepare)
    ok = perform_request({"episode": "ep1", "speed": 0}, library, "KODI-7", None,
                         config={"voice": {"provider": "piper"}})
    captured = capsys.readouterr()
    assert ok is True
    assert "hello stream" in captured.out  # the show aired anyway
    assert "silent show" in captured.err


# ── replay_stop: operator can interrupt a queued/running show ───────────────
def test_perform_request_clears_stale_stop_file_before_and_after(
        library, monkeypatch, tmp_path, fake_performer):
    stop_file = tmp_path / "stop.json"
    stop_file.write_text("{}", encoding="utf-8")  # stale, from a PREVIOUS airing
    monkeypatch.setenv("REPLAY_STOP_FILE", str(stop_file))

    ok = perform_request({"episode": "ep1", "speed": 0}, library, "KODI-7", None)

    assert ok is True
    pacer = FakePerformer.instances[0].kwargs["pacer"]
    assert pacer.should_stop() is False  # stale file cleared before performing
    assert not stop_file.exists()  # consumed again after performing


def test_perform_request_pacer_should_stop_reflects_stop_file(
        library, monkeypatch, tmp_path, fake_performer):
    stop_file = tmp_path / "stop.json"
    monkeypatch.setenv("REPLAY_STOP_FILE", str(stop_file))

    perform_request({"episode": "ep1", "speed": 0}, library, "KODI-7", None)

    pacer = FakePerformer.instances[0].kwargs["pacer"]
    assert pacer.should_stop() is False
    stop_file.write_text("{}", encoding="utf-8")
    assert pacer.should_stop() is True


# ── publish_narration: the durable transcript (docs/revoice.md) ─────────────
class FakeBusProducer:
    def __init__(self, bootstrap_servers, topic):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic

    def send(self, message):
        FakeBusProducer.sent.append(message)
        return message


def _voiced_show():
    return [
        {"kind": "boss", "speaker": "boss", "narration": "ship the login fix"},
        {"kind": "coder_talk", "speaker": "coder", "narration": "on it"},
    ]


def test_publish_narration_sends_one_message_with_all_scenes(monkeypatch):
    FakeBusProducer.sent = []
    monkeypatch.setattr(replay_pane, "MessageProducer", FakeBusProducer)
    config = {"message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages",
                              "worker_id": "coder"}}

    publish_narration(_voiced_show(), config, "ep1", "KODI-7")

    assert len(FakeBusProducer.sent) == 1
    msg = FakeBusProducer.sent[0]
    assert msg["type"] == "replay_narration"
    assert msg["from"] == "coder"
    assert msg["payload"]["episode"] == "ep1"
    assert [s["text"] for s in msg["payload"]["scenes"]] == ["ship the login fix", "on it"]
    assert [s["speaker"] for s in msg["payload"]["scenes"]] == ["boss", "coder"]


def test_publish_narration_skips_when_show_is_none(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("must not construct a producer for a silent show")

    monkeypatch.setattr(replay_pane, "MessageProducer", explode)
    publish_narration(None, {"message_bus": {"bootstrap_servers": "k", "topic": "t"}}, "ep1", "KODI-7")


def test_publish_narration_skips_without_message_bus_config(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("must not construct a producer without message_bus config")

    monkeypatch.setattr(replay_pane, "MessageProducer", explode)
    publish_narration(_voiced_show(), {"voice": {"provider": "piper"}}, "ep1", "KODI-7")


def test_publish_narration_failure_is_swallowed(monkeypatch, capsys):
    class ExplodingProducer:
        def __init__(self, *a, **kw):
            raise RuntimeError("kafka down")

    monkeypatch.setattr(replay_pane, "MessageProducer", ExplodingProducer)
    config = {"message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages"}}

    publish_narration(_voiced_show(), config, "ep1", "KODI-7")  # must not raise

    assert "publish failed" in capsys.readouterr().err


def test_publish_narration_returns_sent_message_id(monkeypatch):
    FakeBusProducer.sent = []
    monkeypatch.setattr(replay_pane, "MessageProducer", FakeBusProducer)
    config = {"message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages"}}

    result = publish_narration(_voiced_show(), config, "ep1", "KODI-7")

    assert result == FakeBusProducer.sent[0]["id"]


def test_publish_narration_returns_none_when_skipped(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("must not construct a producer without message_bus config")

    monkeypatch.setattr(replay_pane, "MessageProducer", explode)
    result = publish_narration(_voiced_show(), {"voice": {"provider": "piper"}}, "ep1", "KODI-7")

    assert result is None


def test_publish_narration_returns_none_on_producer_failure(monkeypatch, capsys):
    class ExplodingProducer:
        def __init__(self, *a, **kw):
            raise RuntimeError("kafka down")

    monkeypatch.setattr(replay_pane, "MessageProducer", ExplodingProducer)
    config = {"message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages"}}

    result = publish_narration(_voiced_show(), config, "ep1", "KODI-7")

    assert result is None


# ── persist_narration: the durable cache for reuse (docs/narration_store.md) ─
def test_persist_narration_skips_silently_when_show_falsy(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("must not touch the store for an empty show")

    monkeypatch.setattr(replay_pane.narration_store, "available", explode)
    persist_narration("mid", None, {}, "ep1", "KODI-7")  # must not raise


def test_persist_narration_prints_notice_when_store_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: False)

    def explode(*a, **kw):
        raise AssertionError("must not save when store is unavailable")

    monkeypatch.setattr(replay_pane.narration_store, "save_airing", explode)

    persist_narration("mid", _voiced_show(), {}, "ep1", "KODI-7")

    assert "not cached for reuse" in capsys.readouterr().out


def test_persist_narration_calls_save_airing_with_given_message_id(monkeypatch):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    calls = {}

    def fake_save(message_id, worker_id, episode, aired_at, show):
        calls["message_id"] = message_id
        calls["worker_id"] = worker_id
        calls["episode"] = episode
        calls["show"] = show
        return len(show)

    monkeypatch.setattr(replay_pane.narration_store, "save_airing", fake_save)

    persist_narration("mid-123", _voiced_show(), {"message_bus": {"worker_id": "coder"}},
                      "ep1", "KODI-7")

    assert calls["message_id"] == "mid-123"
    assert calls["worker_id"] == "coder"
    assert calls["episode"] == "ep1"
    assert calls["show"] == _voiced_show()


def test_persist_narration_generates_uuid_when_message_id_none(monkeypatch):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    calls = {}

    def fake_save(message_id, worker_id, episode, aired_at, show):
        calls["message_id"] = message_id
        return len(show)

    monkeypatch.setattr(replay_pane.narration_store, "save_airing", fake_save)

    persist_narration(None, _voiced_show(), {}, "ep1", "KODI-7")

    assert uuid.UUID(calls["message_id"])  # raises ValueError if not a valid uuid


def test_persist_narration_save_failure_is_swallowed(monkeypatch, capsys):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)

    def explode(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(replay_pane.narration_store, "save_airing", explode)

    persist_narration("mid", _voiced_show(), {}, "ep1", "KODI-7")  # must not raise

    assert "narration cache save failed" in capsys.readouterr().err


# ── load_reused_show: rebuild a voiced show from the cache ───────────────────
def _reuse_script():
    return {"source": "ep1", "events": [{"type": "assistant_text", "text": "hello stream"}]}


def test_load_reused_show_none_when_store_unavailable(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: False)

    result = load_reused_show(_reuse_script(), "ep1", tmp_path)

    assert result is None
    assert "generating fresh narration" in capsys.readouterr().out


def test_load_reused_show_none_when_nothing_cached(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane.narration_store, "load_latest_airing", lambda episode: None)

    result = load_reused_show(_reuse_script(), "ep1", tmp_path)

    assert result is None
    assert "generating fresh" in capsys.readouterr().out


def test_load_reused_show_returns_scenes_with_cached_text_and_no_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    rows = [{"scene_index": 0, "scene_kind": "coder_talk", "speaker": "coder",
             "text": "cached line", "audio": None, "audio_duration_s": None}]
    monkeypatch.setattr(replay_pane.narration_store, "load_latest_airing", lambda episode: rows)

    result = load_reused_show(_reuse_script(), "ep1", tmp_path)

    assert result is not None
    assert len(result) == 1
    assert result[0]["kind"] == "coder_talk"
    assert result[0]["narration"] == "cached line"
    assert result[0]["audio"] is None


def test_load_reused_show_writes_audio_file_and_sets_duration(monkeypatch, tmp_path):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    audio_bytes = b"fake-wav-bytes-for-scene-zero"
    rows = [{"scene_index": 0, "scene_kind": "coder_talk", "speaker": "coder",
             "text": "cached line", "audio": audio_bytes, "audio_duration_s": 3.25}]
    monkeypatch.setattr(replay_pane.narration_store, "load_latest_airing", lambda episode: rows)
    workdir = tmp_path / "work"
    workdir.mkdir()

    result = load_reused_show(_reuse_script(), "ep1", workdir)

    assert result is not None
    scene = result[0]
    assert scene["audio"].duration == 3.25
    assert scene["audio"].audio_path.parent == workdir
    assert scene["audio"].audio_path.read_bytes() == audio_bytes


def test_load_reused_show_none_when_scene_count_mismatch(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    rows = [
        {"scene_index": 0, "scene_kind": "coder_talk", "speaker": "coder",
         "text": "a", "audio": None, "audio_duration_s": None},
        {"scene_index": 1, "scene_kind": "boss", "speaker": "boss",
         "text": "b", "audio": None, "audio_duration_s": None},
    ]
    monkeypatch.setattr(replay_pane.narration_store, "load_latest_airing", lambda episode: rows)

    result = load_reused_show(_reuse_script(), "ep1", tmp_path)

    assert result is None
    assert "no longer matches" in capsys.readouterr().out


def test_load_reused_show_none_when_scene_kind_mismatch(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    rows = [{"scene_index": 0, "scene_kind": "boss", "speaker": "coder",
             "text": "a", "audio": None, "audio_duration_s": None}]
    monkeypatch.setattr(replay_pane.narration_store, "load_latest_airing", lambda episode: rows)

    result = load_reused_show(_reuse_script(), "ep1", tmp_path)

    assert result is None
    assert "no longer matches" in capsys.readouterr().out


def test_load_reused_show_none_when_load_raises(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)

    def explode(episode):
        raise RuntimeError("db down")

    monkeypatch.setattr(replay_pane.narration_store, "load_latest_airing", explode)

    result = load_reused_show(_reuse_script(), "ep1", tmp_path)

    assert result is None
    assert "narration cache load failed" in capsys.readouterr().err


def test_load_reused_show_speaker_comes_from_fresh_plan_scenes_not_row(monkeypatch, tmp_path):
    """Regression test for the multi-speaker duet override (docs/revoice.md):
    `_rebuild_scenes_from_rows` (shared by load_reused_show and the duet
    follower path) matches cached rows to scenes purely on `scene_kind` — it
    never reads a "speaker" key off the row at all. That's *why* narration
    reuse keeps working correctly for a hand-authored multi-speaker script:
    every call recomputes scenes fresh from plan_scenes(script), so a
    scene's speaker always reflects the SCRIPT's own "speaker" tags, never
    whatever (possibly stale, possibly absent) speaker info a cached row
    happens to carry."""
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    script = {
        "source": "multi_ep",
        "events": [
            {"type": "assistant_text", "text": "hi", "speaker": "tester"},
            {"type": "assistant_text", "text": "yo", "speaker": "coder-native"},
        ],
    }
    # Rows deliberately carry NO "speaker" key at all — if
    # _rebuild_scenes_from_rows ever started reading row["speaker"] this
    # would raise KeyError (or, with .get, silently leak stale/missing
    # speaker info) instead of the script's own tags, catching the
    # regression immediately.
    rows = [
        {"scene_index": 0, "scene_kind": "coder_talk",
         "text": "cached line one", "audio": None, "audio_duration_s": None},
        {"scene_index": 1, "scene_kind": "coder_talk",
         "text": "cached line two", "audio": None, "audio_duration_s": None},
    ]
    monkeypatch.setattr(replay_pane.narration_store, "load_latest_airing", lambda episode: rows)

    result = load_reused_show(script, "multi_ep", tmp_path)

    assert result is not None
    assert [scene["speaker"] for scene in result] == ["tester", "coder-native"]
    assert [scene["narration"] for scene in result] == ["cached line one", "cached line two"]


def test_perform_request_publishes_narration_after_voiced_show(library, monkeypatch):
    def fake_prepare(script, config, workdir, **kwargs):
        return [dict(scene, events=[]) for scene in _voiced_show()]

    FakeBusProducer.sent = []
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", fake_prepare)
    monkeypatch.setattr(replay_pane, "MessageProducer", FakeBusProducer)
    config = {"voice": {"provider": "piper"},
              "message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages"}}

    ok = perform_request({"episode": "ep1", "speed": 0}, library, "KODI-7", None, config=config)

    assert ok is True
    assert len(FakeBusProducer.sent) == 1
    assert FakeBusProducer.sent[0]["payload"]["episode"] == "ep1"


# ── perform_request: narration "reuse" wiring ────────────────────────────────
def test_perform_request_reuse_hit_skips_fresh_generation_and_publish(library, capsys, monkeypatch):
    def fake_reused(script, episode, workdir):
        return [{"kind": "coder_talk", "speaker": "coder", "narration": "cached line",
                 "audio": None, "events": []}]

    monkeypatch.setattr(replay_pane, "load_reused_show", fake_reused)

    def explode_prepare(*a, **kw):
        raise AssertionError("must not prepare a fresh voiced show on a reuse hit")

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", explode_prepare)

    def explode_publish(*a, **kw):
        raise AssertionError("must not publish on a reuse hit")

    monkeypatch.setattr(replay_pane, "publish_narration", explode_publish)

    def explode_persist(*a, **kw):
        raise AssertionError("must not persist on a reuse hit")

    monkeypatch.setattr(replay_pane, "persist_narration", explode_persist)

    ok = perform_request({"episode": "ep1", "speed": 0, "narration": "reuse"},
                         library, "KODI-7", None, config={"voice": {"provider": "piper"}})

    out = capsys.readouterr().out
    assert ok is True
    assert "cached line" in out


def test_perform_request_reuse_miss_falls_back_to_fresh_generation(library, monkeypatch):
    monkeypatch.setattr(replay_pane, "load_reused_show", lambda *a, **kw: None)

    def fake_prepare(script, config, workdir, **kwargs):
        return [dict(scene, events=[]) for scene in _voiced_show()]

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", fake_prepare)
    calls = {}

    def fake_publish(show, config, episode, worker_name):
        calls["publish_show"] = show
        return "msg-id-123"

    def fake_persist(message_id, show, config, episode, worker_name):
        calls["persist_message_id"] = message_id
        calls["persist_show"] = show

    monkeypatch.setattr(replay_pane, "publish_narration", fake_publish)
    monkeypatch.setattr(replay_pane, "persist_narration", fake_persist)

    ok = perform_request({"episode": "ep1", "speed": 0, "narration": "reuse"},
                         library, "KODI-7", None, config={"voice": {"provider": "piper"}})

    assert ok is True
    assert calls["persist_message_id"] == "msg-id-123"
    assert calls["persist_show"] == calls["publish_show"]


def test_perform_request_voice_false_blocks_reuse_too(library, monkeypatch):
    def explode_reuse(*a, **kw):
        raise AssertionError("must not attempt reuse when voice is False")

    def explode_prepare(*a, **kw):
        raise AssertionError("must not prepare voice when voice is False")

    monkeypatch.setattr(replay_pane, "load_reused_show", explode_reuse)
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", explode_prepare)

    ok = perform_request({"episode": "ep1", "speed": 0, "voice": False, "narration": "reuse"},
                         library, "KODI-7", None, config={"voice": {"provider": "piper"}})

    assert ok is True


def test_load_worker_config_missing_or_bad_returns_none(tmp_path, capsys):
    assert load_worker_config(tmp_path / "nope.yaml") is None
    bad = tmp_path / "bad.yaml"
    bad.write_text("{{not yaml", encoding="utf-8")
    assert load_worker_config(bad) is None


def test_list_episodes_sorted_and_empty_for_missing_dir(library, tmp_path):
    (library / "another.json").write_text("{}", encoding="utf-8")
    assert list_episodes(library) == ["another", "ep1"]
    assert list_episodes(tmp_path / "nope") == []


# ── agent handler: replay_request ────────────────────────────────────────────
@pytest.fixture
def request_file(tmp_path, monkeypatch):
    path = tmp_path / "replay_request.json"
    monkeypatch.setenv("REPLAY_REQUEST_FILE", str(path))
    return path


def test_replay_request_registered_in_dispatch_table():
    assert MESSAGE_HANDLERS["replay_request"] is handle_replay_request


def test_handle_replay_request_writes_request_and_confirms(request_file):
    producer = FakeProducer()
    msg = {"from": "operator", "type": "replay_request",
           "payload": {"episode": "2026-07-02_04-27-00_6ecdde82", "speed": 2}}

    handle_replay_request("coder", {}, None, producer, msg)

    request = json.loads(request_file.read_text(encoding="utf-8"))
    assert request == {"episode": "2026-07-02_04-27-00_6ecdde82", "speed": 2}
    assert len(producer.sent) == 1
    reply = producer.sent[0]
    assert reply["to"] == "operator"
    assert reply["type"] == "operator_reply"
    assert "2026-07-02_04-27-00_6ecdde82" in reply["payload"]["narration"]


def test_handle_replay_request_missing_episode_replies_error(request_file):
    producer = FakeProducer()
    msg = {"from": "operator", "type": "replay_request", "payload": {}}

    handle_replay_request("coder", {}, None, producer, msg)

    assert not request_file.exists()
    assert "episode" in producer.sent[0]["payload"]["error"]


def test_handle_replay_request_unwritable_path_replies_error(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAY_REQUEST_FILE", str(tmp_path / "no" / "such" / "dir" / "r.json"))
    producer = FakeProducer()
    msg = {"type": "replay_request", "payload": {"episode": "ep1"}}

    handle_replay_request("coder", {}, None, producer, msg)

    assert "could not queue replay" in producer.sent[0]["payload"]["error"]


@pytest.mark.parametrize("voice_value", [True, False])
def test_handle_replay_request_forwards_bool_voice(request_file, voice_value):
    producer = FakeProducer()
    msg = {"from": "operator", "type": "replay_request",
           "payload": {"episode": "ep1", "voice": voice_value}}

    handle_replay_request("coder", {}, None, producer, msg)

    request = json.loads(request_file.read_text(encoding="utf-8"))
    assert request["voice"] is voice_value


def test_handle_replay_request_omits_voice_when_absent(request_file):
    producer = FakeProducer()
    msg = {"from": "operator", "type": "replay_request", "payload": {"episode": "ep1"}}

    handle_replay_request("coder", {}, None, producer, msg)

    request = json.loads(request_file.read_text(encoding="utf-8"))
    assert "voice" not in request


def test_handle_replay_request_omits_voice_when_not_bool(request_file):
    producer = FakeProducer()
    msg = {"from": "operator", "type": "replay_request",
           "payload": {"episode": "ep1", "voice": "yes"}}

    handle_replay_request("coder", {}, None, producer, msg)

    request = json.loads(request_file.read_text(encoding="utf-8"))
    assert "voice" not in request


def test_handle_replay_request_forwards_narration_reuse(request_file):
    producer = FakeProducer()
    msg = {"from": "operator", "type": "replay_request",
           "payload": {"episode": "ep1", "narration": "reuse"}}

    handle_replay_request("coder", {}, None, producer, msg)

    request = json.loads(request_file.read_text(encoding="utf-8"))
    assert request["narration"] == "reuse"


def test_handle_replay_request_omits_narration_when_absent(request_file):
    producer = FakeProducer()
    msg = {"from": "operator", "type": "replay_request", "payload": {"episode": "ep1"}}

    handle_replay_request("coder", {}, None, producer, msg)

    request = json.loads(request_file.read_text(encoding="utf-8"))
    assert "narration" not in request


# ── Duet replay (docs/duet_replay.md) — mode detection, director, follower ──
class RecordingProducer:
    """Fake MessageProducer that records everything sent through ONE
    instance — unlike FakeBusProducer above (class-level .sent), the duet
    director/follower paths build exactly one producer via
    replay_pane._build_bus_producer and reuse it for every invite/ready/cue/
    end publish, so tests inspect that single instance's history."""

    def __init__(self, bootstrap_servers, topic):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


def _duet_config(self_id):
    return {"message_bus": {"bootstrap_servers": "kafka:9092", "topic": "vtuber.messages",
                            "worker_id": self_id}}


def _recording_producer_ctor(holder):
    """MessageProducer stand-in that stashes the single instance it builds
    into `holder["producer"]` so the test can inspect it after the call."""
    def ctor(bootstrap_servers, topic):
        producer = RecordingProducer(bootstrap_servers, topic)
        holder["producer"] = producer
        return producer
    return ctor


class FakeAudio:
    """Minimal duck-typed stand-in for tts_client.Narration — only
    `.duration` is read by the director's ownership-annotation loop."""

    def __init__(self, duration):
        self.duration = duration
        self.audio_path = Path("fake.wav")


def _fake_voiced_show_factory(with_audio=False):
    """A prepare_voiced_show() stand-in returning a 2-scene show matching
    duet_library's script (one "boss" scene, one "coder" scene)."""
    def fake(script, config, workdir, **kwargs):
        scenes = []
        for kind, speaker, text, duration in (
            ("boss", "boss", "boss line", 0.02),
            ("coder_talk", "coder", "coder line", 0.03),
        ):
            scenes.append({
                "kind": kind, "speaker": speaker, "narration": text,
                "events": [{"type": "assistant_text", "text": text}],
                "audio": FakeAudio(duration) if with_audio else None,
            })
        return scenes
    return fake


class FakePerformer:
    """Records constructor kwargs and, on .perform(), walks `show` calling
    on_scene_start(i) / wait_for_scene(i) for each scene index — enough to
    exercise the duet cue-publish and ratchet wiring without any real
    timing, pacing, or audio playback (which the real Performer/Pacer would
    otherwise pull in via replay.play_wav)."""
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.performed_show = None
        self.performed_script = None
        self.aborted = None
        FakePerformer.instances.append(self)

    def perform(self, script, show=None, start=0, limit=None):
        self.performed_script = script
        self.performed_show = show
        on_scene_start = self.kwargs.get("on_scene_start")
        wait_for_scene = self.kwargs.get("wait_for_scene")
        scenes = show or []
        for i in range(len(scenes)):
            if on_scene_start is not None:
                on_scene_start(i)
            if wait_for_scene is not None:
                if wait_for_scene(i) == -1:
                    self.aborted = True
                    return False
        self.aborted = False
        return True  # mirrors the real Performer.perform()'s bool contract


class CapturingPerformer:
    """Records constructor kwargs WITHOUT executing perform() at all — used
    when a test wants to drive wait_for_scene itself under tight control
    (the cue-ratchet edge cases)."""
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.performed_show = None
        CapturingPerformer.instances.append(self)

    def perform(self, script, show=None, start=0, limit=None):
        self.performed_show = show


@pytest.fixture
def duet_library(tmp_path):
    lib = tmp_path / "replays"
    lib.mkdir()
    script = {
        "source": "duet_ep",
        "events": [
            {"type": "user_message", "text": "ship the login fix"},
            {"type": "assistant_text", "text": "on it boss"},
        ],
    }
    (lib / "duet_ep.json").write_text(json.dumps(script), encoding="utf-8")
    return lib


def _duet_rows(boss_audio=None, coder_audio=None, boss_duration=None, coder_duration=None):
    """Rows shaped like narration_store.load_airing(), matching duet_library's
    plan_scenes() output (scene 0 = boss, scene 1 = coder_talk)."""
    return [
        {"scene_index": 0, "scene_kind": "boss", "speaker": "boss", "text": "boss line",
         "audio": boss_audio, "audio_duration_s": boss_duration},
        {"scene_index": 1, "scene_kind": "coder_talk", "speaker": "coder", "text": "coder line",
         "audio": coder_audio, "audio_duration_s": coder_duration},
    ]


@pytest.fixture
def fake_performer(monkeypatch):
    FakePerformer.instances = []
    monkeypatch.setattr(replay_pane, "Performer", FakePerformer)
    return FakePerformer


@pytest.fixture
def capturing_performer(monkeypatch):
    CapturingPerformer.instances = []
    monkeypatch.setattr(replay_pane, "Performer", CapturingPerformer)
    return CapturingPerformer


@pytest.fixture
def duet_timeouts(monkeypatch):
    """Tiny timeouts/poll intervals (docs/duet_replay.md constants) so the
    director's ready-wait and the follower's cue-ratchet watchdog resolve
    near-instantly in tests instead of sleeping for real seconds."""
    monkeypatch.setattr(replay_pane, "REPLAY_READY_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(replay_pane, "REPLAY_READY_TIMEOUT_DEFAULT_S", 0.15)
    monkeypatch.setattr(replay_pane, "REPLAY_CUE_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(replay_pane, "REPLAY_FIRST_CUE_TIMEOUT_S", 0.15)
    monkeypatch.setattr(replay_pane, "REPLAY_WATCHDOG_MIN_S", 0.1)
    monkeypatch.setattr(replay_pane, "REPLAY_WATCHDOG_GRACE_S", 0.05)


@pytest.fixture
def relay_files(tmp_path, monkeypatch):
    cue_file = tmp_path / "duet_cue.json"
    ready_file = tmp_path / "duet_ready.json"
    monkeypatch.setenv("REPLAY_CUE_FILE", str(cue_file))
    monkeypatch.setenv("REPLAY_READY_FILE", str(ready_file))
    return {"cue_file": cue_file, "ready_file": ready_file}


# ── resolve_self_id ───────────────────────────────────────────────────────────
def test_resolve_self_id_prefers_config_worker_id(monkeypatch):
    monkeypatch.delenv("WORKER_ID", raising=False)
    config = {"message_bus": {"worker_id": "from-config"}}
    assert resolve_self_id(config, "from-arg") == "from-config"


def test_resolve_self_id_falls_back_to_env_when_no_config(monkeypatch):
    monkeypatch.setenv("WORKER_ID", "from-env")
    assert resolve_self_id(None, "from-arg") == "from-env"
    assert resolve_self_id({}, "from-arg") == "from-env"


def test_resolve_self_id_falls_back_to_worker_name(monkeypatch):
    monkeypatch.delenv("WORKER_ID", raising=False)
    assert resolve_self_id(None, "from-arg") == "from-arg"
    assert resolve_self_id({"message_bus": {}}, "from-arg") == "from-arg"


# ── mode detection (perform_request dispatch) ────────────────────────────────
def test_perform_request_mode_follow_dispatches_to_follower(monkeypatch):
    monkeypatch.delenv("WORKER_ID", raising=False)
    calls = {}

    def fake_follower(request, library, worker_name, state_path, self_id,
                      default_speed=1.0, config=None):
        calls["args"] = (request, worker_name, self_id)
        return True

    def explode_director(*a, **kw):
        raise AssertionError("mode=follow must not dispatch to the director path")

    monkeypatch.setattr(replay_pane, "perform_follower_request", fake_follower)
    monkeypatch.setattr(replay_pane, "perform_director_request", explode_director)

    request = {"mode": "follow", "airing_id": "a1", "episode": "duet_ep", "cast": {}}
    ok = perform_request(request, "lib", "KODI-7", None)

    assert ok is True
    assert calls["args"] == (request, "KODI-7", "KODI-7")


def test_perform_request_cast_with_other_worker_dispatches_to_director(monkeypatch):
    monkeypatch.delenv("WORKER_ID", raising=False)
    calls = {}

    def fake_director(request, library, worker_name, state_path, self_id,
                      default_speed=1.0, config=None):
        calls["self_id"] = self_id
        return True

    def explode_follower(*a, **kw):
        raise AssertionError("a director cast must not dispatch to the follower path")

    monkeypatch.setattr(replay_pane, "perform_director_request", fake_director)
    monkeypatch.setattr(replay_pane, "perform_follower_request", explode_follower)

    request = {"episode": "ep1", "cast": {"coder": "worker-B"}}
    ok = perform_request(request, "lib", "KODI-7", None)

    assert ok is True
    assert calls["self_id"] == "KODI-7"


def test_perform_request_cast_all_self_is_solo(library, monkeypatch, capsys):
    monkeypatch.delenv("WORKER_ID", raising=False)

    def explode_director(*a, **kw):
        raise AssertionError("a cast whose values are all this worker's own id is solo")

    def explode_follower(*a, **kw):
        raise AssertionError("no mode=follow ⇒ must not dispatch to the follower path")

    monkeypatch.setattr(replay_pane, "perform_director_request", explode_director)
    monkeypatch.setattr(replay_pane, "perform_follower_request", explode_follower)

    request = {"episode": "ep1", "speed": 0, "cast": {"coder": "KODI-7", "boss": "KODI-7"}}
    ok = perform_request(request, library, "KODI-7", None)

    assert ok is True
    assert "hello stream" in capsys.readouterr().out


def test_perform_request_empty_cast_is_solo(library, monkeypatch, capsys):
    monkeypatch.delenv("WORKER_ID", raising=False)

    def explode_director(*a, **kw):
        raise AssertionError("an empty cast is solo")

    monkeypatch.setattr(replay_pane, "perform_director_request", explode_director)

    ok = perform_request({"episode": "ep1", "speed": 0, "cast": {}}, library, "KODI-7", None)

    assert ok is True
    assert "hello stream" in capsys.readouterr().out


# ── director path ─────────────────────────────────────────────────────────────
def test_director_invites_distinct_followers_deduped_and_cues_in_order(
        duet_library, monkeypatch, fake_performer, duet_timeouts, relay_files):
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", _fake_voiced_show_factory())
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **kw: "msg-1")
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **kw: "airing-123")
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    # Both speakers cast to the SAME follower — invites must dedupe to one.
    cast = {"boss": "workerB", "coder": "workerB"}
    request = {"episode": "duet_ep", "cast": cast, "speed": 1000}
    relay_files["ready_file"].write_text(
        json.dumps({"airing_id": "airing-123", "workers": ["workerB"]}), encoding="utf-8")

    ok = perform_director_request(request, duet_library, "director-1", None, "director-1",
                                  config=_duet_config("director-1"))

    assert ok is True
    producer = holder["producer"]
    invites = [m for m in producer.sent if m["type"] == "replay_invite"]
    assert len(invites) == 1
    assert invites[0]["to"] == "workerB"
    assert invites[0]["payload"] == {
        "airing_id": "airing-123", "episode": "duet_ep", "cast": cast,
        "speed": 1000.0, "worker_name": "director-1", "director": "director-1",
    }
    cues = [m for m in producer.sent if m["type"] == "replay_cue"]
    assert [c["payload"]["scene_index"] for c in cues] == [0, 1]
    assert all(c["to"] == "workerB" and c["payload"]["airing_id"] == "airing-123" for c in cues)
    ends = [m for m in producer.sent if m["type"] == "replay_end"]
    assert len(ends) == 1
    assert ends[0]["to"] == "workerB"
    assert ends[0]["payload"] == {"airing_id": "airing-123", "reason": "finished"}


def test_director_ready_timeout_refuses_without_performing(
        duet_library, monkeypatch, fake_performer, duet_timeouts, relay_files):
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", _fake_voiced_show_factory())
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **kw: "msg-1")
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **kw: "airing-999")
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))
    # ready file is left empty — nobody ever becomes ready.

    request = {"episode": "duet_ep", "cast": {"boss": "workerB"}, "speed": 1000}
    ok = perform_director_request(request, duet_library, "director-1", None, "director-1",
                                  config=_duet_config("director-1"))

    assert ok is False
    assert FakePerformer.instances == []  # never performed
    producer = holder["producer"]
    ends = [m for m in producer.sent if m["type"] == "replay_end"]
    assert len(ends) == 1
    assert ends[0]["to"] == "workerB"
    assert ends[0]["payload"]["reason"] == "ready_timeout"
    assert ends[0]["payload"]["airing_id"] == "airing-999"
    errors = [m for m in producer.sent if m["type"] == "operator_reply"]
    assert len(errors) == 1
    assert errors[0]["to"] == "operator"
    assert "error" in errors[0]["payload"]


def test_director_tells_followers_stopped_when_show_is_cut_short(
        duet_library, monkeypatch, duet_timeouts, relay_files):
    """When the Performer's should_stop hook fires mid-show, perform()
    returns False (docs/replay.md) — the director must relay that as
    reason "stopped", not "finished" (docs/duet_replay.md)."""
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", _fake_voiced_show_factory())
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **kw: "msg-1")
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **kw: "airing-123")
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    class StoppedPerformer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def perform(self, script, show=None, start=0, limit=None):
            return False  # simulates an operator replay_stop firing mid-show

    monkeypatch.setattr(replay_pane, "Performer", StoppedPerformer)

    request = {"episode": "duet_ep", "cast": {"boss": "workerB"}, "speed": 1000}
    relay_files["ready_file"].write_text(
        json.dumps({"airing_id": "airing-123", "workers": ["workerB"]}), encoding="utf-8")

    ok = perform_director_request(request, duet_library, "director-1", None, "director-1",
                                  config=_duet_config("director-1"))

    assert ok is True  # an episode DID air, just cut short
    producer = holder["producer"]
    ends = [m for m in producer.sent if m["type"] == "replay_end"]
    assert ends[0]["payload"] == {"airing_id": "airing-123", "reason": "stopped"}


def test_director_refuses_with_stopped_reason_when_stop_arrives_before_ready(
        duet_library, monkeypatch, fake_performer, duet_timeouts, relay_files, tmp_path):
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", _fake_voiced_show_factory())
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **kw: "msg-1")
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **kw: "airing-55")
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    stop_file = tmp_path / "stop.json"
    monkeypatch.setenv("REPLAY_STOP_FILE", str(stop_file))

    def fake_sleep(seconds):
        # Simulates an operator replay_stop landing partway through the
        # director's ready-wait poll loop (never actually sleeps real time).
        stop_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(replay_pane.time, "sleep", fake_sleep)
    # ready_file is left empty — nobody ever becomes ready; the stop must
    # win before the ready_timeout would otherwise fire.

    request = {"episode": "duet_ep", "cast": {"boss": "workerB"}, "speed": 1000}
    ok = perform_director_request(request, duet_library, "director-1", None, "director-1",
                                  config=_duet_config("director-1"))

    assert ok is False
    assert FakePerformer.instances == []  # never performed
    producer = holder["producer"]
    ends = [m for m in producer.sent if m["type"] == "replay_end"]
    assert ends[0]["payload"]["reason"] == "stopped"
    assert ends[0]["payload"]["airing_id"] == "airing-55"


def test_director_refuses_when_store_unavailable(
        duet_library, monkeypatch, fake_performer, duet_timeouts, relay_files):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: False)

    def explode_prepare(*a, **kw):
        raise AssertionError("must not prepare voice when the narration store is unavailable")

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", explode_prepare)
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    request = {"episode": "duet_ep", "cast": {"boss": "workerB"}, "speed": 1000}
    ok = perform_director_request(request, duet_library, "director-1", None, "director-1",
                                  config=_duet_config("director-1"))

    assert ok is False
    assert FakePerformer.instances == []
    producer = holder["producer"]
    assert [m for m in producer.sent if m["type"] == "replay_invite"] == []
    assert [m for m in producer.sent if m["type"] == "replay_end"] == []  # nobody invited yet
    errors = [m for m in producer.sent if m["type"] == "operator_reply"]
    assert len(errors) == 1
    assert "narration store" in errors[0]["payload"]["error"]


def test_director_refuses_when_producer_unavailable(duet_library, monkeypatch, fake_performer,
                                                     duet_timeouts, relay_files, capsys):
    def explode_prepare(*a, **kw):
        raise AssertionError("must not prepare voice when no bus producer can be built")

    monkeypatch.setattr(replay_pane, "prepare_voiced_show", explode_prepare)
    # No message_bus config at all ⇒ _build_bus_producer returns None.
    request = {"episode": "duet_ep", "cast": {"boss": "workerB"}, "speed": 1000}

    ok = perform_director_request(request, duet_library, "director-1", None, "director-1",
                                  config={})

    assert ok is False
    assert FakePerformer.instances == []
    assert "duet refused" in capsys.readouterr().err


def test_director_annotates_ownership_and_strips_unowned_audio(
        duet_library, monkeypatch, fake_performer, duet_timeouts, relay_files):
    monkeypatch.setattr(replay_pane, "prepare_voiced_show", _fake_voiced_show_factory(with_audio=True))
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane, "publish_narration", lambda *a, **kw: "msg-1")
    monkeypatch.setattr(replay_pane, "persist_narration", lambda *a, **kw: "airing-42")
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    # Director voices "boss" itself; "coder" belongs to workerB.
    cast = {"boss": "director-1", "coder": "workerB"}
    request = {"episode": "duet_ep", "cast": cast, "speed": 1000}
    relay_files["ready_file"].write_text(
        json.dumps({"airing_id": "airing-42", "workers": ["workerB"]}), encoding="utf-8")

    ok = perform_director_request(request, duet_library, "director-1", None, "director-1",
                                  config=_duet_config("director-1"))

    assert ok is True
    show = FakePerformer.instances[0].performed_show
    boss_scene = next(s for s in show if s["speaker"] == "boss")
    coder_scene = next(s for s in show if s["speaker"] == "coder")
    assert boss_scene["owned"] is True
    assert boss_scene["audio"] is not None
    assert boss_scene["target_duration"] == pytest.approx(0.02)
    assert coder_scene["owned"] is False
    assert coder_scene["audio"] is None  # stripped: not this director's speaker
    assert coder_scene["target_duration"] == pytest.approx(0.03)  # kept for pacing


# ── follower path ─────────────────────────────────────────────────────────────
def test_follower_happy_path_loads_owned_audio_and_notifies_director(
        duet_library, monkeypatch, fake_performer, duet_timeouts, relay_files):
    rows = _duet_rows(boss_audio=b"boss-wav-bytes", coder_audio=b"coder-wav-bytes",
                      boss_duration=1.5, coder_duration=2.5)
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane.narration_store, "load_airing", lambda airing_id: rows)
    holder = {}
    monkeypatch.setattr(replay_pane, "MessageProducer", _recording_producer_ctor(holder))

    cast = {"boss": "director-1", "coder": "follower-1"}
    request = {"mode": "follow", "airing_id": "airing-77", "episode": "duet_ep", "cast": cast,
              "speed": 1000, "worker_name": "KODI-Follower", "director": "director-1"}

    ok = perform_follower_request(request, duet_library, "follower-1", None, "follower-1",
                                  config=_duet_config("follower-1"))

    assert ok is True
    show = FakePerformer.instances[0].performed_show
    boss_scene = next(s for s in show if s["speaker"] == "boss")
    coder_scene = next(s for s in show if s["speaker"] == "coder")
    assert boss_scene["owned"] is False
    assert boss_scene["audio"] is None  # not this follower's scene — never written to disk
    assert boss_scene["target_duration"] == 1.5
    assert coder_scene["owned"] is True
    assert coder_scene["audio"] is not None
    assert coder_scene["audio"].duration == 2.5
    assert coder_scene["target_duration"] == 2.5

    producer = holder["producer"]
    ready_msgs = [m for m in producer.sent if m["type"] == "replay_ready"]
    assert len(ready_msgs) == 1
    assert ready_msgs[0]["to"] == "director-1"
    assert ready_msgs[0]["payload"] == {"airing_id": "airing-77"}


def test_follower_missing_airing_returns_to_idle_without_performing(
        duet_library, monkeypatch, fake_performer, duet_timeouts, relay_files, capsys):
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane.narration_store, "load_airing", lambda airing_id: None)

    request = {"mode": "follow", "airing_id": "missing-id", "episode": "duet_ep",
              "cast": {"boss": "director-1", "coder": "follower-1"}, "director": "director-1"}
    ok = perform_follower_request(request, duet_library, "follower-1", None, "follower-1")

    assert ok is False
    assert FakePerformer.instances == []
    assert "no cached airing" in capsys.readouterr().err


def test_follower_scene_mismatch_returns_to_idle_without_performing(
        duet_library, monkeypatch, fake_performer, duet_timeouts, relay_files, capsys):
    rows = [{"scene_index": 0, "scene_kind": "coder_talk", "speaker": "coder", "text": "x",
             "audio": None, "audio_duration_s": None}]  # duet_ep's script has TWO scenes
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane.narration_store, "load_airing", lambda airing_id: rows)

    request = {"mode": "follow", "airing_id": "airing-1", "episode": "duet_ep",
              "cast": {"boss": "director-1", "coder": "follower-1"}, "director": "director-1"}
    ok = perform_follower_request(request, duet_library, "follower-1", None, "follower-1")

    assert ok is False
    assert FakePerformer.instances == []
    assert "no longer matches" in capsys.readouterr().err


def test_follower_wait_for_scene_ratchet(duet_library, monkeypatch, capturing_performer,
                                         duet_timeouts, relay_files):
    rows = _duet_rows(boss_duration=1.0, coder_duration=1.0)
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane.narration_store, "load_airing", lambda airing_id: rows)
    monkeypatch.setattr(replay_pane, "MessageProducer", RecordingProducer)

    request = {"mode": "follow", "airing_id": "airing-1", "episode": "duet_ep",
              "cast": {"boss": "director-1", "coder": "follower-1"}, "director": "director-1"}
    ok = perform_follower_request(request, duet_library, "follower-1", None, "follower-1",
                                  config=_duet_config("follower-1"))
    assert ok is True
    wait_for_scene = CapturingPerformer.instances[0].kwargs["wait_for_scene"]
    cue_file = relay_files["cue_file"]

    # A cue ahead of the scene we're waiting for authorizes proceeding to it.
    cue_file.write_text(json.dumps({"airing_id": "airing-1", "type": "cue", "scene_index": 3}),
                        encoding="utf-8")
    assert wait_for_scene(1) == 3

    # An "end" for the right airing stops the show.
    cue_file.write_text(json.dumps({"airing_id": "airing-1", "type": "end", "reason": "finished"}),
                        encoding="utf-8")
    assert wait_for_scene(0) == -1

    # A cue for a DIFFERENT airing is ignored — the watchdog eventually fires.
    cue_file.write_text(json.dumps({"airing_id": "some-other-airing", "type": "cue",
                                    "scene_index": 5}), encoding="utf-8")
    assert wait_for_scene(0) == -1

    # No cue file at all — same watchdog timeout applies.
    cue_file.unlink()
    assert wait_for_scene(0) == -1


def test_follower_wait_for_scene_stops_immediately_on_stop_file(
        duet_library, monkeypatch, capturing_performer, duet_timeouts, relay_files, tmp_path):
    """An operator replay_stop reaches a follower directly through
    wait_for_scene (not just via the director's own replay_end relay) —
    docs/duet_replay.md."""
    rows = _duet_rows(boss_duration=1.0, coder_duration=1.0)
    monkeypatch.setattr(replay_pane.narration_store, "available", lambda: True)
    monkeypatch.setattr(replay_pane.narration_store, "load_airing", lambda airing_id: rows)
    monkeypatch.setattr(replay_pane, "MessageProducer", RecordingProducer)

    stop_file = tmp_path / "stop.json"
    monkeypatch.setenv("REPLAY_STOP_FILE", str(stop_file))

    request = {"mode": "follow", "airing_id": "airing-1", "episode": "duet_ep",
              "cast": {"boss": "director-1", "coder": "follower-1"}, "director": "director-1"}
    ok = perform_follower_request(request, duet_library, "follower-1", None, "follower-1",
                                  config=_duet_config("follower-1"))
    assert ok is True
    wait_for_scene = CapturingPerformer.instances[0].kwargs["wait_for_scene"]

    stop_file.write_text("{}", encoding="utf-8")
    assert wait_for_scene(0) == -1
