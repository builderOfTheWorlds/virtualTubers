"""Tests for app/relay_io.py — the shared file-IPC helpers (docs/relay_io.md)."""
import json
import os
import threading

import pytest

import relay_io


def _names(directory):
    return sorted(p.name for p in directory.iterdir())


# ── path resolution ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("resolver, env, default", [
    (relay_io.resolve_replay_request_file, "REPLAY_REQUEST_FILE", "/tmp/replay_request.json"),
    (relay_io.resolve_replay_stop_file, "REPLAY_STOP_FILE", "/tmp/replay_stop.json"),
    (relay_io.resolve_replay_cue_file, "REPLAY_CUE_FILE", "/tmp/replay_cue.json"),
    (relay_io.resolve_replay_ready_file, "REPLAY_READY_FILE", "/tmp/replay_ready.json"),
    (relay_io.resolve_tile_relay_dir, "TILE_RELAY_DIR", "/tmp/tiles"),
])
def test_resolvers_keep_env_names_and_defaults(monkeypatch, resolver, env, default):
    monkeypatch.delenv(env, raising=False)
    assert resolver() == default
    monkeypatch.setenv(env, "")  # empty means "use the default"
    assert resolver() == default
    monkeypatch.setenv(env, "/custom/path")
    assert resolver() == "/custom/path"


def test_tile_relay_dir_explicit_argument_wins(monkeypatch):
    monkeypatch.setenv("TILE_RELAY_DIR", "/from/env")
    assert relay_io.resolve_tile_relay_dir("/explicit") == "/explicit"


# ── atomic write ─────────────────────────────────────────────────────────────
def test_atomic_write_round_trips_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "cue.json"
    relay_io.atomic_write_json(str(path), {"airing_id": "a", "scene_index": 1})
    relay_io.atomic_write_json(path, {"airing_id": "a", "scene_index": 2}, fsync=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"airing_id": "a", "scene_index": 2}
    assert _names(tmp_path) == ["cue.json"]


def test_atomic_write_wire_format_is_plain_json_dump(tmp_path):
    """Bytes on disk must match the old json.dump(data, f) exactly."""
    path = tmp_path / "r.json"
    data = {"episode": "ép", "speed": 1.5, "cast": {"coder": "w1"}}
    relay_io.atomic_write_json(path, data)
    assert path.read_text(encoding="utf-8") == json.dumps(data)


def test_atomic_write_missing_dir_raises_oserror_and_leaves_nothing(tmp_path):
    with pytest.raises(OSError):
        relay_io.atomic_write_json(tmp_path / "nope" / "x.json", {"a": 1})
    assert _names(tmp_path) == []


def test_atomic_write_unserializable_data_cleans_up_temp(tmp_path):
    with pytest.raises(TypeError):
        relay_io.atomic_write_json(tmp_path / "x.json", {"a": object()})
    assert _names(tmp_path) == []


def test_atomic_write_failed_replace_cleans_up_temp(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("replace failed")
    monkeypatch.setattr(relay_io.os, "replace", boom)
    with pytest.raises(OSError):
        relay_io.atomic_write_json(tmp_path / "x.json", {"a": 1})
    assert _names(tmp_path) == []


def test_atomic_write_respects_umask_not_mkstemp_0600(tmp_path):
    path = tmp_path / "x.json"
    old = os.umask(0o022)
    try:
        relay_io.atomic_write_json(path, {})
    finally:
        os.umask(old)
    assert (path.stat().st_mode & 0o777) == 0o644


def test_concurrent_writers_never_expose_partial_json(tmp_path):
    """Many threads hammer one path while a reader parses it continuously:
    every read must be complete JSON from exactly one writer, no writer may
    fail (the old shared "<path>.tmp" name made os.replace steal another
    writer's temp file -> FileNotFoundError), and no temp files may leak."""
    path = tmp_path / "shared.json"
    relay_io.atomic_write_json(path, {"writer": -1, "i": -1, "pad": ""})
    errors, bad_reads = [], []
    stop = threading.Event()
    pad = "x" * 20000  # big enough that a non-atomic write would be caught mid-way

    def writer(n):
        try:
            for i in range(60):
                relay_io.atomic_write_json(path, {"writer": n, "i": i, "pad": pad})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reader():
        while not stop.is_set():
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                bad_reads.append("vanished")
                continue
            try:
                data = json.loads(text)
            except ValueError:
                bad_reads.append(text[:40])
                continue
            if data["writer"] != -1 and data["pad"] != pad:
                bad_reads.append("torn")

    r = threading.Thread(target=reader)
    r.start()
    writers = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    r.join()

    assert errors == []
    assert bad_reads == []
    assert _names(tmp_path) == ["shared.json"]


# ── atomic create (write-if-absent) ─────────────────────────────────────────
def test_atomic_create_writes_when_absent_and_refuses_when_present(tmp_path):
    path = tmp_path / "req.json"
    assert relay_io.atomic_create_json(path, {"episode": "one"}) is True
    assert relay_io.atomic_create_json(path, {"episode": "two"}) is False
    assert json.loads(path.read_text(encoding="utf-8")) == {"episode": "one"}
    assert _names(tmp_path) == ["req.json"]


def test_atomic_create_exactly_one_of_many_concurrent_creators_wins(tmp_path):
    path = tmp_path / "req.json"
    results = []
    barrier = threading.Barrier(10)

    def create(n):
        barrier.wait()
        results.append((n, relay_io.atomic_create_json(path, {"n": n})))

    threads = [threading.Thread(target=create, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [n for n, ok in results if ok]
    assert len(winners) == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"n": winners[0]}
    assert _names(tmp_path) == ["req.json"]


def test_atomic_create_falls_back_when_hard_links_unsupported(tmp_path, monkeypatch):
    def no_links(src, dst):
        raise PermissionError("links not supported")
    monkeypatch.setattr(relay_io.os, "link", no_links)
    path = tmp_path / "req.json"
    assert relay_io.atomic_create_json(path, {"a": 1}) is True
    assert relay_io.atomic_create_json(path, {"a": 2}) is False
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert _names(tmp_path) == ["req.json"]


# ── tolerant read ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("content", [
    "", "{", '{"airing_id": "a", "type": "cu', "not json at all", b"\xff\xfe\x00garbage",
])
def test_read_json_tolerates_partial_and_garbage(tmp_path, content):
    path = tmp_path / "cue.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    assert relay_io.read_json(path) is None


def test_read_json_missing_file_and_missing_dir_return_none(tmp_path):
    assert relay_io.read_json(tmp_path / "absent.json") is None
    assert relay_io.read_json(tmp_path / "no" / "dir.json") is None


def test_read_json_directory_in_place_of_file_returns_none(tmp_path):
    (tmp_path / "cue.json").mkdir()
    assert relay_io.read_json(tmp_path / "cue.json") is None


def test_read_json_returns_parsed_value(tmp_path):
    path = tmp_path / "ready.json"
    path.write_text('{"airing_id": "a", "workers": ["w1"]}', encoding="utf-8")
    assert relay_io.read_json(path) == {"airing_id": "a", "workers": ["w1"]}


# ── consume ──────────────────────────────────────────────────────────────────
def test_consume_returns_data_once_and_removes_file(tmp_path):
    path = tmp_path / "req.json"
    relay_io.atomic_write_json(path, {"episode": "e"})
    assert relay_io.consume_json(path) == (True, {"episode": "e"}, None)
    assert relay_io.consume_json(path) == (False, None, None)
    assert _names(tmp_path) == []


def test_consume_garbage_is_found_removed_and_reports_error(tmp_path):
    path = tmp_path / "req.json"
    path.write_text("{ nope", encoding="utf-8")
    result = relay_io.consume_json(path)
    assert result.found is True and result.data is None
    assert isinstance(result.error, ValueError)
    assert _names(tmp_path) == []


def test_consume_missing_dir_is_nothing_pending(tmp_path):
    assert relay_io.consume_json(tmp_path / "no" / "req.json") == (False, None, None)


def test_consume_never_deletes_a_request_written_while_reading(tmp_path, monkeypatch):
    """The race the old read-then-unlink had: request B lands while A is
    being read; the consumer must return A and leave B for the next poll."""
    path = tmp_path / "req.json"
    relay_io.atomic_write_json(path, {"episode": "A"})
    real_load = relay_io._load

    def load_then_new_request_arrives(p):
        result = real_load(p)
        relay_io.atomic_write_json(path, {"episode": "B"})
        return result

    monkeypatch.setattr(relay_io, "_load", load_then_new_request_arrives)
    assert relay_io.consume_json(path).data == {"episode": "A"}
    monkeypatch.setattr(relay_io, "_load", real_load)
    assert relay_io.consume_json(path).data == {"episode": "B"}
    assert _names(tmp_path) == []


def test_consume_is_exactly_once_across_concurrent_consumers(tmp_path):
    path = tmp_path / "req.json"
    for round_no in range(25):
        relay_io.atomic_write_json(path, {"round": round_no})
        got = []
        barrier = threading.Barrier(6)

        def consume():
            barrier.wait()
            result = relay_io.consume_json(path)
            if result.found:
                got.append(result.data)

        threads = [threading.Thread(target=consume) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert got == [{"round": round_no}]
    assert _names(tmp_path) == []


# ── cleanup ──────────────────────────────────────────────────────────────────
def test_remove_file_reports_whether_it_removed(tmp_path):
    path = tmp_path / "stop.json"
    path.write_text("{}", encoding="utf-8")
    assert relay_io.remove_file(path) is True
    assert relay_io.remove_file(path) is False


def test_remove_file_propagates_other_errors(tmp_path):
    (tmp_path / "d.json").mkdir()
    with pytest.raises(OSError):
        relay_io.remove_file(tmp_path / "d.json")


def test_delete_stale_is_unconditional_and_never_raises(tmp_path):
    path = tmp_path / "cue.json"
    relay_io.atomic_write_json(path, {"airing_id": "old", "type": "end"})
    assert relay_io.delete_stale(path) is True
    assert relay_io.delete_stale(path) is False
    assert relay_io.delete_stale(tmp_path / "no" / "dir.json") is False
    (tmp_path / "d.json").mkdir()
    assert relay_io.delete_stale(tmp_path / "d.json") is False  # logged, not raised


@pytest.mark.parametrize("content, removed", [
    ({"airing_id": "old", "type": "end"}, True),        # another airing: stale
    ({"airing_id": "now", "type": "cue", "scene_index": 0}, False),  # ours: keep
    (["not", "a", "dict"], True),
    (None, True),                                        # garbage file
])
def test_delete_stale_keep_airing_id(tmp_path, content, removed):
    path = tmp_path / "cue.json"
    if content is None:
        path.write_text("{ garbage", encoding="utf-8")
    else:
        relay_io.atomic_write_json(path, content)
    assert relay_io.delete_stale(path, keep_airing_id="now") is removed
    assert path.exists() is (not removed)
    if not removed:
        assert json.loads(path.read_text(encoding="utf-8")) == content
    assert [n for n in _names(tmp_path) if n != "cue.json"] == []


def test_delete_stale_keep_airing_id_missing_file(tmp_path):
    assert relay_io.delete_stale(tmp_path / "cue.json", keep_airing_id="now") is False


@pytest.mark.parametrize("content, removed", [
    ({"airing_id": "mine", "type": "end"}, True),
    ({"airing_id": "next", "type": "cue", "scene_index": 0}, False),
    (None, True),
])
def test_delete_if_airing(tmp_path, content, removed):
    path = tmp_path / "cue.json"
    if content is None:
        path.write_text("", encoding="utf-8")
    else:
        relay_io.atomic_write_json(path, content)
    assert relay_io.delete_if_airing(path, "mine") is removed
    assert path.exists() is (not removed)


def test_remove_if_newer_file_written_mid_check_wins(tmp_path, monkeypatch):
    """A writer replacing the file while remove_if inspects the claimed copy
    must not be undone by the restore."""
    path = tmp_path / "cue.json"
    relay_io.atomic_write_json(path, {"airing_id": "now", "scene_index": 0})

    def keep_but_new_cue_lands(data):
        relay_io.atomic_write_json(path, {"airing_id": "now", "scene_index": 1})
        return False

    assert relay_io.remove_if(path, keep_but_new_cue_lands) is False
    assert json.loads(path.read_text(encoding="utf-8"))["scene_index"] == 1
    assert _names(tmp_path) == ["cue.json"]


def test_remove_if_raising_predicate_keeps_file(tmp_path):
    path = tmp_path / "cue.json"
    relay_io.atomic_write_json(path, {"a": 1})

    def boom(data):
        raise RuntimeError("bug")

    assert relay_io.remove_if(path, boom) is False
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert _names(tmp_path) == ["cue.json"]
