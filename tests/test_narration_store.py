"""Tests for app/narration_store.py — direct Postgres persistence for voiced
replay narrations (docs/narration_store.md). No real DB: _connect() is always
monkeypatched to a fake connection/cursor recording what would be sent."""
import struct
import sys
import types
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import narration_store  # noqa: E402
from tts_client import Narration  # noqa: E402


def write_wav(path, seconds=1.0, rate=8000):
    """A real (silent) WAV of a known duration, so audio_path.read_bytes()
    returns genuine WAV bytes rather than a stand-in string."""
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))


def _ensure_psycopg2_importable(monkeypatch):
    """Most dev boxes here have the real psycopg2 installed, but keep the
    test suite honest for an environment where it isn't: inject a minimal
    fake module so `import psycopg2` (and psycopg2.Binary) still works."""
    try:
        import psycopg2  # noqa: F401
        return
    except ImportError:
        pass

    fake = types.ModuleType("psycopg2")

    class Binary:
        def __init__(self, data):
            self.adapted = data

    fake.Binary = Binary
    fake.connect = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "psycopg2", fake)


class FakeCursor:
    def __init__(self, fetch_rows=None):
        self.calls = []
        self._fetch_rows = fetch_rows if fetch_rows is not None else []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self._fetch_rows

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeConn:
    def __init__(self, fetch_rows=None):
        self.closed = False
        self.cur = FakeCursor(fetch_rows)

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


# ── available() ───────────────────────────────────────────────────────────────

def test_available_false_when_env_vars_missing(monkeypatch):
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    assert narration_store.available() is False


@pytest.mark.parametrize("missing", ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"])
def test_available_false_when_one_env_var_missing(monkeypatch, missing):
    monkeypatch.setenv("POSTGRES_DB", "vtuber")
    monkeypatch.setenv("POSTGRES_USER", "vtuber")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.delenv(missing, raising=False)
    assert narration_store.available() is False


def test_available_true_when_env_set_and_psycopg2_importable(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "vtuber")
    monkeypatch.setenv("POSTGRES_USER", "vtuber")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    _ensure_psycopg2_importable(monkeypatch)
    assert narration_store.available() is True


def test_available_false_when_psycopg2_not_importable(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "vtuber")
    monkeypatch.setenv("POSTGRES_USER", "vtuber")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    # Force `import psycopg2` to raise ImportError regardless of whether the
    # real package is installed in this venv.
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    assert narration_store.available() is False


# ── save_airing ───────────────────────────────────────────────────────────────

def test_save_airing_writes_one_row_per_scene_and_closes_connection(monkeypatch, tmp_path):
    _ensure_psycopg2_importable(monkeypatch)
    wav_path = tmp_path / "scene_000.wav"
    write_wav(wav_path, seconds=1.5)
    audio_bytes = wav_path.read_bytes()

    show = [
        {
            "kind": "coder_talk",
            "speaker": "coder",
            "narration": "on it",
            "audio": Narration(audio_path=wav_path, duration=1.5),
        },
        {
            "kind": "boss",
            "speaker": "boss",
            "narration": "ship the login fix",
            "audio": None,
        },
    ]

    fake_conn = FakeConn()
    monkeypatch.setattr(narration_store, "_connect", lambda: fake_conn)

    result = narration_store.save_airing(
        "msg-1", "coder", "ep1", "2026-07-12T00:00:00+00:00", show,
    )

    assert result == 2
    assert len(fake_conn.cur.calls) == 2
    assert fake_conn.closed is True

    sql0, params0 = fake_conn.cur.calls[0]
    assert "INSERT INTO voiced_narration" in sql0
    assert params0["message_id"] == "msg-1"
    assert params0["worker_id"] == "coder"
    assert params0["episode"] == "ep1"
    assert params0["aired_at"] == "2026-07-12T00:00:00+00:00"
    assert params0["scene_index"] == 0
    assert params0["scene_kind"] == "coder_talk"
    assert params0["speaker"] == "coder"
    assert params0["text"] == "on it"
    assert params0["audio"].adapted == audio_bytes
    assert params0["audio_duration_s"] == 1.5

    _, params1 = fake_conn.cur.calls[1]
    assert params1["scene_index"] == 1
    assert params1["scene_kind"] == "boss"
    assert params1["speaker"] == "boss"
    assert params1["text"] == "ship the login fix"
    assert params1["audio"] is None
    assert params1["audio_duration_s"] is None


def test_save_airing_closes_connection_even_when_execute_raises(monkeypatch, tmp_path):
    _ensure_psycopg2_importable(monkeypatch)
    wav_path = tmp_path / "scene_000.wav"
    write_wav(wav_path, seconds=0.5)
    show = [{"kind": "coder_talk", "speaker": "coder", "narration": "hi",
             "audio": Narration(audio_path=wav_path, duration=0.5)}]

    fake_conn = FakeConn()

    def explode(sql, params=None):
        raise RuntimeError("db exploded")

    fake_conn.cur.execute = explode
    monkeypatch.setattr(narration_store, "_connect", lambda: fake_conn)

    with pytest.raises(RuntimeError):
        narration_store.save_airing("msg-1", "coder", "ep1", "now", show)

    assert fake_conn.closed is True


# ── load_latest_airing ────────────────────────────────────────────────────────

def test_load_latest_airing_returns_dicts_with_message_id_and_bytes_audio(monkeypatch):
    rows = [
        ("msg-9", 0, "coder_talk", "coder", "line one", memoryview(b"wav-bytes-one"), 1.5),
        ("msg-9", 1, "boss", "boss", "line two", None, None),
    ]
    fake_conn = FakeConn(fetch_rows=rows)
    monkeypatch.setattr(narration_store, "_connect", lambda: fake_conn)

    result = narration_store.load_latest_airing("ep1")

    assert fake_conn.closed is True
    assert result == [
        {"message_id": "msg-9", "scene_index": 0, "scene_kind": "coder_talk",
         "speaker": "coder", "text": "line one", "audio": b"wav-bytes-one",
         "audio_duration_s": 1.5},
        {"message_id": "msg-9", "scene_index": 1, "scene_kind": "boss",
         "speaker": "boss", "text": "line two", "audio": None,
         "audio_duration_s": None},
    ]
    assert isinstance(result[0]["audio"], bytes)


def test_load_latest_airing_returns_none_when_nothing_cached(monkeypatch):
    fake_conn = FakeConn(fetch_rows=[])
    monkeypatch.setattr(narration_store, "_connect", lambda: fake_conn)

    result = narration_store.load_latest_airing("ep1")

    assert result is None
    assert fake_conn.closed is True


# ── load_airing ──────────────────────────────────────────────────────────────

def test_load_airing_returns_dicts_for_exact_message_id(monkeypatch):
    rows = [
        ("msg-1", 0, "coder_talk", "coder", "line one", memoryview(b"wav-bytes"), 1.5),
        ("msg-1", 1, "boss", "boss", "line two", None, None),
    ]
    fake_conn = FakeConn(fetch_rows=rows)
    monkeypatch.setattr(narration_store, "_connect", lambda: fake_conn)

    result = narration_store.load_airing("msg-1")

    assert fake_conn.closed is True
    assert result == [
        {"message_id": "msg-1", "scene_index": 0, "scene_kind": "coder_talk",
         "speaker": "coder", "text": "line one", "audio": b"wav-bytes",
         "audio_duration_s": 1.5},
        {"message_id": "msg-1", "scene_index": 1, "scene_kind": "boss",
         "speaker": "boss", "text": "line two", "audio": None,
         "audio_duration_s": None},
    ]
    assert isinstance(result[0]["audio"], bytes)

    sql, params = fake_conn.cur.calls[0]
    assert "WHERE message_id = %(message_id)s" in sql
    assert params == {"message_id": "msg-1"}


def test_load_airing_returns_none_when_message_id_unknown(monkeypatch):
    fake_conn = FakeConn(fetch_rows=[])
    monkeypatch.setattr(narration_store, "_connect", lambda: fake_conn)

    result = narration_store.load_airing("nope")

    assert result is None
    assert fake_conn.closed is True


def test_load_airing_raises_on_db_failure(monkeypatch):
    fake_conn = FakeConn()

    def explode(sql, params=None):
        raise RuntimeError("db exploded")

    fake_conn.cur.execute = explode
    monkeypatch.setattr(narration_store, "_connect", lambda: fake_conn)

    with pytest.raises(RuntimeError):
        narration_store.load_airing("msg-1")

    assert fake_conn.closed is True
