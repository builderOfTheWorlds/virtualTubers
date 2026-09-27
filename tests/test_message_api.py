"""
Tests for services/message-api/api.py.
Requires services/message-api/requirements.txt installed (fastapi, uvicorn,
redis) in addition to the root requirements.txt (kafka-python).
KafkaProducer and redis.Redis are mocked at import time so these tests never
touch a real broker or Redis instance.
"""
import json
import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
import redis
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "message-api"))
sys.path.insert(0, str(ROOT / "app"))

# api needs these at import time only. Restore the environment afterwards:
# leaving POSTGRES_* set for the rest of the session made
# generation_store.available() report True in unrelated suites, so their
# real-Postgres tests ran against localhost:5432 instead of skipping.
_IMPORT_ENV = {
    "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
    "KAFKA_TOPIC": "test-topic",
    "POSTGRES_DB": "virtualtubers",
    "POSTGRES_USER": "virtualtubers",
    "POSTGRES_PASSWORD": "secret",
}
_added_env = [k for k in _IMPORT_ENV if k not in os.environ]
for _k in _added_env:
    os.environ[_k] = _IMPORT_ENV[_k]

try:
    with patch("message_bus.KafkaProducer"), \
         patch("worker_control.redis.Redis.from_url"), \
         patch("log_filter_control.redis.Redis.from_url"):
        import api
finally:
    for _k in _added_env:
        os.environ.pop(_k, None)


@pytest.fixture
def client():
    api.producer.send = MagicMock()
    api.control._client = MagicMock()
    api.log_filter._client = MagicMock()
    return TestClient(api.app)


def test_post_message_valid_input(client):
    resp = client.post("/messages", json={"to": "coder", "payload": {"task": "hi"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["from"] == "operator"
    assert body["to"] == "coder"
    assert body["type"] == "operator_message"
    assert body["payload"] == {"task": "hi"}
    api.producer.send.assert_called_once()


def test_post_message_custom_type(client):
    resp = client.post("/messages", json={"to": "coder", "type": "task_assignment", "payload": {}})
    assert resp.status_code == 200
    assert resp.json()["type"] == "task_assignment"


def test_post_message_missing_required_field(client):
    resp = client.post("/messages", json={"payload": {}})
    assert resp.status_code == 422


def test_get_worker_status_defaults_enabled(client):
    api.control._client.get.return_value = None
    resp = client.get("/workers/coder")
    assert resp.status_code == 200
    assert resp.json() == {"worker_id": "coder", "enabled": True}


def test_disable_then_enable_worker_round_trip(client):
    resp = client.post("/workers/coder/disable")
    assert resp.status_code == 200
    assert resp.json() == {"worker_id": "coder", "enabled": False}
    api.control._client.set.assert_called_with("worker:coder:enabled", "0")

    resp = client.post("/workers/coder/enable")
    assert resp.status_code == 200
    assert resp.json() == {"worker_id": "coder", "enabled": True}
    api.control._client.set.assert_called_with("worker:coder:enabled", "1")


def test_disable_worker_returns_503_when_redis_unavailable(client):
    api.control._client.set.side_effect = redis.RedisError("connection refused")
    resp = client.post("/workers/coder/disable")
    assert resp.status_code == 503


def _health(worker_id, state="alive", **fields):
    row = {"worker_id": worker_id, "state": state, "alive": state in ("alive", "stale"),
           "last_seen": None, "age_s": None, "local_override": None, "enabled": True, "ttl_s": None}
    row.update(fields)
    return row


def test_get_worker_health_returns_control_health_row(client, monkeypatch):
    fake = MagicMock(return_value=_health("coder", local_override=True, age_s=3.0))
    monkeypatch.setattr(api.control, "health", fake)
    resp = client.get("/workers/coder/health")
    assert resp.status_code == 200
    assert resp.json()["local_override"] is True
    fake.assert_called_once_with("coder")


def test_get_all_workers_health_uses_known_list_plus_redis_discovered(client, monkeypatch):
    monkeypatch.setattr(api.control, "known_worker_ids", MagicMock(return_value=["coder", "roundtable"]))
    fake = MagicMock(side_effect=lambda ids: [_health(w) for w in ids])
    monkeypatch.setattr(api.control, "health_many", fake)
    resp = client.get("/workers/health")
    assert resp.status_code == 200
    ids = [r["worker_id"] for r in resp.json()["workers"]]
    assert ids == list(api.WORKER_ID_EXAMPLES) + ["roundtable"]


def test_workers_health_is_not_captured_as_worker_id(client):
    """/workers/health must hit the health route, not GET /workers/{id}."""
    api.control._client.mget.return_value = [None] * 2 * len(api.WORKER_ID_EXAMPLES)
    api.control._client.scan_iter.return_value = iter([])
    resp = client.get("/workers/health")
    assert resp.status_code == 200
    assert "workers" in resp.json()


def test_workers_health_reports_unknown_not_503_when_redis_down(client):
    api.control._client.mget.side_effect = redis.ConnectionError("refused")
    api.control._client.scan_iter.side_effect = redis.ConnectionError("refused")
    resp = client.get("/workers/health")
    assert resp.status_code == 200
    rows = resp.json()["workers"]
    assert rows and all(r["state"] == "unknown" and r["alive"] is None and r["enabled"] is None for r in rows)

    resp = client.get("/workers/coder/health")
    assert resp.status_code == 200
    assert resp.json()["alive"] is None


def test_worker_health_enabled_uses_redis_flag_not_local_kill_file(client, monkeypatch, tmp_path):
    kill = tmp_path / "worker_disabled"
    kill.write_text("x")
    monkeypatch.setattr(api.control, "kill_file", str(kill))
    api.control._client.mget.return_value = [None, None]  # no alive key, no enabled key
    body = client.get("/workers/coder/health").json()
    assert body["enabled"] is True
    assert body["state"] == "down"


def test_get_log_filter_defaults_excluded_for_status_update(client):
    api.log_filter._client.get.return_value = None
    resp = client.get("/log-filter/status_update")
    assert resp.status_code == 200
    assert resp.json() == {"type": "status_update", "excluded": True}


def test_get_log_filter_defaults_not_excluded_for_other_types(client):
    api.log_filter._client.get.return_value = None
    resp = client.get("/log-filter/task_complete")
    assert resp.status_code == 200
    assert resp.json() == {"type": "task_complete", "excluded": False}


def test_include_then_exclude_log_type_round_trip(client):
    resp = client.post("/log-filter/status_update/include")
    assert resp.status_code == 200
    assert resp.json() == {"type": "status_update", "excluded": False}
    api.log_filter._client.set.assert_called_with("logfilter:status_update:excluded", "0")

    resp = client.post("/log-filter/status_update/exclude")
    assert resp.status_code == 200
    assert resp.json() == {"type": "status_update", "excluded": True}
    api.log_filter._client.set.assert_called_with("logfilter:status_update:excluded", "1")


def test_exclude_log_type_returns_503_when_redis_unavailable(client):
    api.log_filter._client.set.side_effect = redis.RedisError("connection refused")
    resp = client.post("/log-filter/status_update/exclude")
    assert resp.status_code == 503


def test_prune_logs_requires_at_least_one_bound(client):
    resp = client.post("/logs/prune", json={})
    assert resp.status_code == 400


def test_prune_logs_deletes_range(client):
    with patch("api.prune_logs", return_value=5) as fake_prune:
        resp = client.post("/logs/prune", json={
            "after": "2026-07-01T00:00:00Z", "before": "2026-07-02T00:00:00Z",
        })

    assert resp.status_code == 200
    assert resp.json()["deleted"] == 5
    fake_prune.assert_called_once()
    _, kwargs = fake_prune.call_args
    assert kwargs["after"].isoformat() == "2026-07-01T00:00:00+00:00"
    assert kwargs["before"].isoformat() == "2026-07-02T00:00:00+00:00"


def test_prune_logs_returns_503_when_postgres_unavailable(client):
    with patch("api.prune_logs", side_effect=psycopg2.OperationalError("connection refused")):
        resp = client.post("/logs/prune", json={"after": "2026-07-01T00:00:00Z"})
    assert resp.status_code == 503


# ── /logs/containers, /logs/messages (docs/replay_logs.md) ─────────────────
def test_get_container_logs_requires_at_least_one_service(client):
    resp = client.get("/logs/containers")
    assert resp.status_code == 422


def test_get_container_logs_passes_services_since_and_limit(client):
    rows = [{"container_name": "virtualtubers-worker-coder-1", "stream": "stdout",
             "message": "hi", "log_timestamp": "2026-08-01T00:00:01+00:00"}]
    with patch("api.fetch_container_logs", return_value=rows) as fake_fetch:
        resp = client.get(
            "/logs/containers",
            params=[("service", "worker-coder"), ("service", "worker-roundtable"),
                    ("since", "2026-08-01T00:00:00Z"), ("limit", "50")],
        )
    assert resp.status_code == 200
    assert resp.json() == {"logs": rows}
    args, kwargs = fake_fetch.call_args
    assert list(args[0]) == ["worker-coder", "worker-roundtable"]
    assert kwargs["limit"] == 50
    assert kwargs["since"].isoformat() == "2026-08-01T00:00:00+00:00"


def test_get_container_logs_passes_contains_filters(client):
    with patch("api.fetch_container_logs", return_value=[]) as fake_fetch:
        resp = client.get(
            "/logs/containers",
            params=[("service", "worker-roundtable"), ("contains", "══ fin ══"), ("contains", "♪ ")],
        )
    assert resp.status_code == 200
    assert fake_fetch.call_args[1]["contains"] == ["══ fin ══", "♪ "]


def test_get_container_logs_503_when_postgres_unavailable(client):
    with patch("api.fetch_container_logs", side_effect=psycopg2.OperationalError("refused")):
        resp = client.get("/logs/containers", params={"service": "worker-coder"})
    assert resp.status_code == 503


def test_get_message_logs_requires_at_least_one_worker_id(client):
    resp = client.get("/logs/messages")
    assert resp.status_code == 422


def test_get_message_logs_passes_worker_ids_since_and_limit(client):
    rows = [{"from": "operator", "to": "coder", "type": "replay_request",
             "payload": {"episode": "demo"}, "timestamp": "2026-08-01T00:00:01+00:00"}]
    with patch("api.fetch_messages", return_value=rows) as fake_fetch:
        resp = client.get(
            "/logs/messages",
            params=[("worker_id", "coder"), ("worker_id", "roundtable"), ("limit", "10")],
        )
    assert resp.status_code == 200
    assert resp.json() == {"messages": rows}
    args, kwargs = fake_fetch.call_args
    assert list(args[0]) == ["coder", "roundtable"]
    assert kwargs["limit"] == 10
    assert kwargs["since"] is None


def test_get_message_logs_503_when_postgres_unavailable(client):
    with patch("api.fetch_messages", side_effect=psycopg2.OperationalError("refused")):
        resp = client.get("/logs/messages", params={"worker_id": "coder"})
    assert resp.status_code == 503


# ── /replays (Rerun Theater episode library) ────────────────────────────────
# episode_store and validate_episode/resolve_name are monkeypatched per test
# (never a real Postgres). _schema_ready is forced True so _ensure_schema()
# doesn't attempt a real DB connection on every request that reaches it.
import episode_validator  # noqa: E402

VALID_SCRIPT = {
    "source": "demo-ep",
    "project": "virtualTubers",
    "session_id": "sess-1",
    "date": "2026-08-01",
    "events": [{"type": "assistant_text", "text": "hi"}] * 5,
}


@pytest.fixture
def no_store(client, monkeypatch):
    """episode_store reachable == False for every /replays endpoint."""
    monkeypatch.setattr(api.episode_store, "available", lambda: False)
    return client


@pytest.mark.parametrize("method,path,kwargs", [
    ("post", "/replays", {"content": b"{}"}),
    ("get", "/replays", {}),
    ("get", "/replays/ep1", {}),
    ("delete", "/replays/ep1", {}),
])
def test_replays_endpoints_503_when_store_unavailable(no_store, method, path, kwargs):
    resp = getattr(no_store, method)(path, **kwargs)
    assert resp.status_code == 503


def test_upload_replay_success_stores_and_returns_info(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})
    save_calls = []
    monkeypatch.setattr(
        api.episode_store, "save_episode",
        lambda name, script, overwrite=False, **kw: save_calls.append((name, script, overwrite, kw)) or True)

    resp = client.post("/replays", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"name": "demo-ep", "event_count": 5, "byte_size": 123,
                    "created": True, "status": "approved"}
    assert len(save_calls) == 1
    assert save_calls[0][0] == "demo-ep"
    assert save_calls[0][2] is False
    # No ?status= means today's behaviour: stored approved, airs immediately.
    assert save_calls[0][3] == {"status": "approved", "uploaded_by": "operator"}


def test_upload_replay_success_with_explicit_json_content_type(client, monkeypatch):
    """Regression test: the README and scripts/build_replay_library.py both
    document `curl -H 'Content-Type: application/json' --data-binary @file`.
    A `bytes = Body(..., media_type=...)` param can get JSON-decoded before
    validation on some fastapi/starlette versions, turning that exact,
    documented call into a 422. Every other test in this file posts with no
    Content-Type header and would not have caught that."""
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})
    monkeypatch.setattr(
        api.episode_store, "save_episode", lambda name, script, overwrite=False, **kw: True)

    resp = client.post(
        "/replays",
        content=json.dumps(VALID_SCRIPT).encode(),
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    assert resp.json()["name"] == "demo-ep"


def test_upload_replay_overwrite_query_param_passed_through(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})
    save_calls = []
    monkeypatch.setattr(
        api.episode_store, "save_episode",
        lambda name, script, overwrite=False, **kw: save_calls.append(overwrite) or True)

    resp = client.post(
        "/replays?overwrite=true", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 200
    assert save_calls == [True]


def test_upload_replay_413_when_over_max_upload_bytes(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 10)

    resp = client.post("/replays", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 413


def test_upload_replay_400_when_body_not_valid_json(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    resp = client.post("/replays", content=b"not json{")

    assert resp.status_code == 400
    assert "not valid json" in resp.json()["detail"]


def test_upload_replay_400_when_episode_invalid(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    def raise_invalid(script, name=None):
        raise episode_validator.EpisodeInvalid("missing required key(s): source")
    monkeypatch.setattr(api, "validate_episode", raise_invalid)

    resp = client.post("/replays", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "missing required key(s): source"


def test_upload_replay_409_when_episode_already_exists(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})
    monkeypatch.setattr(api.episode_store, "save_episode", lambda *a, **kw: False)

    resp = client.post("/replays", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 409
    assert "demo-ep" in resp.json()["detail"]


def test_upload_replay_503_when_save_raises_operational_error(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})

    def raise_op_error(*a, **kw):
        raise psycopg2.OperationalError("connection refused")
    monkeypatch.setattr(api.episode_store, "save_episode", raise_op_error)

    resp = client.post("/replays", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 503


def test_list_replays_returns_detailed_episode_metadata(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    episodes = [{"name": "ep1", "project": "virtualTubers", "session_id": "s1",
                 "date": "2026-08-01", "event_count": 5, "byte_size": 123,
                 "uploaded_by": "operator", "uploaded_at": "2026-08-01T00:00:00+00:00"}]
    monkeypatch.setattr(api.episode_store, "list_episodes_detailed", lambda status="approved": episodes)

    resp = client.get("/replays")

    assert resp.status_code == 200
    assert resp.json() == {"episodes": episodes}


def test_list_replays_503_when_listing_raises_operational_error(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)

    def raise_op_error(status="approved"):
        raise psycopg2.OperationalError("connection refused")
    monkeypatch.setattr(api.episode_store, "list_episodes_detailed", raise_op_error)

    resp = client.get("/replays")

    assert resp.status_code == 503


def test_get_replay_returns_the_stored_script(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api.episode_store, "load_episode", lambda name, include_drafts=False: VALID_SCRIPT)

    resp = client.get("/replays/demo-ep")

    assert resp.status_code == 200
    assert resp.json() == VALID_SCRIPT


def test_get_replay_404_when_episode_missing(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api.episode_store, "load_episode", lambda name, include_drafts=False: None)

    resp = client.get("/replays/nope")

    assert resp.status_code == 404


def test_get_replay_400_when_name_has_disallowed_characters(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    # A "/" would split into extra path segments and 404 on routing before
    # ever reaching the handler; a space is invalid per NAME_RE but stays a
    # single path segment, so it actually exercises _safe_name's rejection.
    resp = client.get("/replays/bad name")

    assert resp.status_code == 400


def test_get_replay_503_when_load_raises_operational_error(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    def raise_op_error(name, include_drafts=False):
        raise psycopg2.OperationalError("connection refused")
    monkeypatch.setattr(api.episode_store, "load_episode", raise_op_error)

    resp = client.get("/replays/demo-ep")

    assert resp.status_code == 503


def test_delete_replay_true_when_episode_removed(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api.episode_store, "delete_episode", lambda name: True)

    resp = client.delete("/replays/demo-ep")

    assert resp.status_code == 200
    assert resp.json() == {"name": "demo-ep", "deleted": True}


def test_delete_replay_false_when_episode_absent(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api.episode_store, "delete_episode", lambda name: False)

    resp = client.delete("/replays/demo-ep")

    assert resp.status_code == 200
    assert resp.json() == {"name": "demo-ep", "deleted": False}


def test_delete_replay_400_when_name_has_disallowed_characters(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    resp = client.delete("/replays/bad name")

    assert resp.status_code == 400


def test_delete_replay_503_when_delete_raises_operational_error(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)

    def raise_op_error(name):
        raise psycopg2.OperationalError("connection refused")
    monkeypatch.setattr(api.episode_store, "delete_episode", raise_op_error)

    resp = client.delete("/replays/demo-ep")

    assert resp.status_code == 503


# ── GM live music control (/music) ───────────────────────────────────────────
@pytest.fixture
def music_redis():
    """MusicControl talks to a mocked redis client for the /music tests."""
    fake = MagicMock()
    original = api.music_control._client
    api.music_control._client = fake
    yield fake
    api.music_control._client = original


def test_list_music_moods_includes_gems_neutral_and_silence(client):
    moods = client.get("/music-moods").json()["moods"]
    assert "tension" in moods and "joyful_activation" in moods
    assert "neutral" in moods and moods[-1] == "silence"
    assert len(moods) == 11


def test_get_music_no_override_no_director(client, music_redis):
    music_redis.get.return_value = None
    body = client.get("/music/roundtable").json()
    assert body == {"worker_id": "roundtable", "override": None, "overridden": False,
                    "running": False, "status": None}


def test_get_music_reports_override_and_status(client, music_redis):
    status = {"theme": "ashiorid", "mood": "tension", "playing_mood": "tension", "tempo_bpm": 120.0}

    def get(key):
        if key == "music:roundtable:override":
            return json.dumps({"mood": "tension", "intensity": 0.8})
        if key == "music:roundtable:status":
            return json.dumps(status)
        return None

    music_redis.get.side_effect = get
    body = client.get("/music/roundtable").json()
    assert body["override"] == {"mood": "tension", "intensity": 0.8}
    assert body["overridden"] is True
    assert body["running"] is True
    assert body["status"] == status


@pytest.mark.parametrize("mood,expected", [("tension", "tension"), (" Silence ", "silence"),
                                           ("joyful_activation", "joyful_activation")])
def test_set_music_valid_mood_writes_override(client, music_redis, mood, expected):
    resp = client.post("/music/roundtable", json={"mood": mood, "intensity": 0.7})
    assert resp.status_code == 200
    assert resp.json() == {"worker_id": "roundtable", "override": {"mood": expected, "intensity": 0.7},
                           "overridden": True}
    key, raw = music_redis.set.call_args.args
    assert key == "music:roundtable:override"
    assert json.loads(raw)["mood"] == expected


def test_set_music_clamps_intensity(client, music_redis):
    resp = client.post("/music/roundtable", json={"mood": "power", "intensity": 3})
    assert resp.json()["override"]["intensity"] == 1.0


def test_set_music_unknown_mood_is_400(client, music_redis):
    resp = client.post("/music/roundtable", json={"mood": "banana"})
    assert resp.status_code == 400
    assert "unknown mood" in resp.json()["detail"]
    music_redis.set.assert_not_called()


def test_set_music_redis_down_is_503(client, music_redis):
    music_redis.set.side_effect = redis.ConnectionError("down")
    resp = client.post("/music/roundtable", json={"mood": "tension"})
    assert resp.status_code == 503


def test_clear_music_deletes_override(client, music_redis):
    resp = client.delete("/music/roundtable")
    assert resp.status_code == 200
    assert resp.json() == {"worker_id": "roundtable", "override": None, "overridden": False}
    music_redis.delete.assert_called_once_with("music:roundtable:override")


def test_clear_music_redis_down_is_503(client, music_redis):
    music_redis.delete.side_effect = redis.ConnectionError("down")
    assert client.delete("/music/roundtable").status_code == 503


def test_music_control_import_does_not_pull_numpy():
    """message-api's image has no numpy — music.control must stay numpy-free."""
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); from music.control import MusicControl; "
            "from music.mood_map import MOODS; assert 'numpy' not in sys.modules, 'numpy imported'"
            % str(ROOT / "app"))
    subprocess.run([sys.executable, "-c", code], check=True)


# ── Draft review gate (docs/episode_store.md "Review status") ──────────────
@pytest.fixture
def store_up(client, monkeypatch):
    monkeypatch.setattr(api.episode_store, "available", lambda: True)
    monkeypatch.setattr(api, "_schema_ready", True)
    monkeypatch.setattr(
        api, "validate_episode",
        lambda script, name=None: {"name": "demo-ep", "event_count": 5, "byte_size": 123})
    return client


@pytest.mark.parametrize("query,expected_status", [
    ("", "approved"),
    ("?status=approved", "approved"),
    ("?status=draft", "draft"),
])
def test_upload_replay_status_query_passed_to_store(store_up, monkeypatch, query, expected_status):
    calls = []
    monkeypatch.setattr(
        api.episode_store, "save_episode",
        lambda name, script, overwrite=False, **kw: calls.append(kw) or True)

    resp = store_up.post("/replays" + query, content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 200
    assert resp.json()["status"] == expected_status
    assert calls[0]["status"] == expected_status


def test_upload_replay_uploaded_by_query_passed_to_store(store_up, monkeypatch):
    calls = []
    monkeypatch.setattr(
        api.episode_store, "save_episode",
        lambda name, script, overwrite=False, **kw: calls.append(kw) or True)

    resp = store_up.post("/replays?status=draft&uploaded_by=3layer-generator",
                         content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 200
    assert calls[0] == {"status": "draft", "uploaded_by": "3layer-generator"}


def test_upload_replay_invalid_status_is_422_and_nothing_saved(store_up, monkeypatch):
    calls = []
    monkeypatch.setattr(
        api.episode_store, "save_episode",
        lambda *a, **kw: calls.append(kw) or True)

    resp = store_up.post("/replays?status=live", content=json.dumps(VALID_SCRIPT).encode())

    assert resp.status_code == 422
    assert calls == []


@pytest.mark.parametrize("query,expected_arg", [
    ("", "approved"),
    ("?status=approved", "approved"),
    ("?status=draft", "draft"),
    ("?status=all", None),
])
def test_list_replays_status_filter_maps_to_store_arg(store_up, monkeypatch, query, expected_arg):
    seen = []
    monkeypatch.setattr(
        api.episode_store, "list_episodes_detailed",
        lambda status="approved": seen.append(status) or [])

    resp = store_up.get("/replays" + query)

    assert resp.status_code == 200
    assert seen == [expected_arg]


def test_list_replays_invalid_status_is_422(store_up):
    assert store_up.get("/replays?status=bogus").status_code == 422


def test_get_replay_includes_drafts_for_review(store_up, monkeypatch):
    seen = []
    monkeypatch.setattr(
        api.episode_store, "load_episode",
        lambda name, include_drafts=False: seen.append(include_drafts) or VALID_SCRIPT)

    resp = store_up.get("/replays/demo-ep")

    assert resp.status_code == 200
    assert seen == [True]


def test_approve_replay_promotes_a_draft(store_up, monkeypatch):
    calls = []
    monkeypatch.setattr(
        api.episode_store, "approve_episode", lambda name: calls.append(name) or "draft")

    resp = store_up.post("/replays/demo-ep/approve")

    assert resp.status_code == 200
    assert resp.json() == {"name": "demo-ep", "status": "approved", "previous_status": "draft"}
    assert calls == ["demo-ep"]


def test_approve_replay_already_approved_is_idempotent(store_up, monkeypatch):
    monkeypatch.setattr(api.episode_store, "approve_episode", lambda name: "approved")

    resp = store_up.post("/replays/demo-ep/approve")

    assert resp.status_code == 200
    assert resp.json()["previous_status"] == "approved"


def test_approve_replay_404_when_episode_missing(store_up, monkeypatch):
    monkeypatch.setattr(api.episode_store, "approve_episode", lambda name: None)

    assert store_up.post("/replays/nope/approve").status_code == 404


def test_approve_replay_400_when_name_has_disallowed_characters(store_up, monkeypatch):
    calls = []
    monkeypatch.setattr(api.episode_store, "approve_episode", lambda name: calls.append(name))

    assert store_up.post("/replays/bad name/approve").status_code == 400
    assert calls == []


def test_approve_replay_503_when_postgres_unavailable(store_up, monkeypatch):
    def raise_op_error(name):
        raise psycopg2.OperationalError("connection refused")
    monkeypatch.setattr(api.episode_store, "approve_episode", raise_op_error)

    assert store_up.post("/replays/demo-ep/approve").status_code == 503


def test_approve_replay_503_when_store_unavailable(no_store):
    assert no_store.post("/replays/demo-ep/approve").status_code == 503
