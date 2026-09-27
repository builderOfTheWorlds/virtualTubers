"""Tests for app/music — theme parsing, mood map, engine, control, recorder,
store. No real DB/Redis/pacat: all mocked."""
import json

import numpy as np
import pytest

from music import engine as engine_mod
from music import music_store
from music.control import (MoodRequest, MoodResolver, parse_override,
                           parse_scene_cue, write_scene_cue, ControlSources)
from music.engine import MusicEngine
from music.mood_map import (MODES, MOODS, blend_moods, interpolate, mood_for_emotion,
                            mood_params, normalize_mood)
from music.recorder import MusicRecorder, encode_audio
from music.theme import ThemeError, load_theme, theme_from_dict, theme_to_dict

THEME = {
    "name": "t", "tonic": "D", "base_octave": 3, "beats_per_bar": 4, "seed": 1,
    "motif": [[1, 1], [5, 1], [6, 2], [0, 1], [3, 3]],
    "progression": [1, 6, 4, 5],
}


@pytest.fixture
def theme():
    return theme_from_dict(THEME)


# ── theme ──────────────────────────────────────────────────────────────────

def test_theme_round_trips_through_dict(theme):
    assert theme_from_dict(theme_to_dict(theme)) == theme


@pytest.mark.parametrize("patch,field", [
    ({"name": ""}, "name"),
    ({"tonic": "H"}, "tonic"),
    ({"motif": []}, "motif"),
    ({"motif": [[0, 1]]}, "rests"),
    ({"motif": [[1, 0]]}, "beats"),
    ({"progression": [9]}, "progression"),
    ({"beats_per_bar": 12}, "beats_per_bar"),
])
def test_theme_invalid_raises_naming_field(patch, field):
    with pytest.raises(ThemeError, match=field):
        theme_from_dict({**THEME, **patch})


def test_shipped_ashiorid_theme_loads():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "campaigns/ashiorid/music/theme.yaml"
    assert load_theme(path).name == "ashiorid"


# ── mood map ───────────────────────────────────────────────────────────────

def test_moods_cover_campaign_gems_vocabulary():
    from campaign.pack import MOODS as PACK_MOODS
    assert set(PACK_MOODS) <= set(MOODS)


def test_sad_is_slower_darker_and_minor_vs_joyful():
    sad, joy = mood_params("sadness"), mood_params("joyful_activation")
    assert sad.tempo_bpm < joy.tempo_bpm
    assert sad.brightness < joy.brightness
    assert MODES[sad.mode][2] == 3      # minor third
    assert MODES[joy.mode][2] == 4      # major third
    assert sad.valence < 0 < joy.valence


def test_normalize_mood_unknown_is_neutral():
    assert normalize_mood("nope") == "neutral"
    assert normalize_mood(None) == "neutral"
    assert normalize_mood(" Tension ") == "tension"


def test_intensity_scales_energy_not_mode():
    lo, hi = mood_params("tension", 0.0), mood_params("tension", 1.0)
    assert lo.mode == hi.mode
    assert lo.tempo_bpm < hi.tempo_bpm
    assert lo.density < hi.density


def test_interpolate_switches_discrete_and_lerps_continuous():
    a, b = mood_params("sadness"), mood_params("power")
    mid = interpolate(a, b, 0.5)
    assert mid.mode == b.mode
    assert mid.tempo_bpm == pytest.approx((a.tempo_bpm + b.tempo_bpm) / 2)
    assert interpolate(a, b, 0) == a


def test_blend_moods_and_emotion_mapping():
    assert blend_moods(["tension", "sadness"]).mode == mood_params("tension").mode
    assert blend_moods([]).mood == "neutral"
    assert mood_for_emotion("sad") == "sadness"
    assert mood_for_emotion("??") == "neutral"


# ── engine ─────────────────────────────────────────────────────────────────

def test_engine_renders_stereo_float_bars_of_expected_length(theme):
    eng = MusicEngine(theme, mood="tension")
    bar = eng.render_bar()
    spb = 60 / bar.params.tempo_bpm
    assert bar.audio.dtype == np.float32 and bar.audio.shape[1] == 2
    assert bar.audio.shape[0] == round(4 * spb * eng.sr)
    assert np.abs(bar.audio).max() < 1.0
    assert bar.notes and all("t" in n for n in bar.notes)


def test_engine_is_deterministic(theme):
    a = MusicEngine(theme, mood="wonder").render_seconds(8)
    b = MusicEngine(theme, mood="wonder").render_seconds(8)
    assert all(np.array_equal(x.audio, y.audio) for x, y in zip(a, b))


def test_motif_is_stated_in_new_mode_after_change(theme):
    eng = MusicEngine(theme, mood="joyful_activation")
    eng.render_seconds(10)
    eng.set_mood("sadness")
    bars = [eng.render_bar() for _ in range(len(theme.progression) * 2)]
    leads = [n for b in bars for n in b.notes if n["layer"] == "lead"]
    assert leads, "theme motif must be heard after a mood change"
    assert bars[-1].params.mode == "aeolian"


def test_degree_mapping_wraps_octaves():
    assert MusicEngine.degree_to_semitone(1, "ionian") == 0
    assert MusicEngine.degree_to_semitone(8, "ionian") == 12
    assert MusicEngine.degree_to_semitone(0, "ionian") == -1


def test_set_mood_noop_returns_false(theme):
    eng = MusicEngine(theme, mood="tension")
    assert eng.set_mood("tension") is False
    assert eng.set_mood("sadness") is True


def test_renders_faster_than_realtime(theme):
    import time
    eng = MusicEngine(theme, mood="joyful_activation")
    t0 = time.perf_counter()
    bars = eng.render_seconds(10)
    assert sum(b.duration_s for b in bars) / (time.perf_counter() - t0) > 2


# ── control ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, None),
    ('{"mood": "follow"}', None),
    ('{"mood": "tension", "intensity": 2}', MoodRequest("tension", 1.0, "gm_override")),
    ('{"mood": "silence"}', MoodRequest("silence", 0.5, "gm_override")),
    ("sadness", MoodRequest("sadness", 0.5, "gm_override")),
])
def test_parse_override(raw, expected):
    assert parse_override(raw) == expected


def test_parse_scene_cue_takes_first_real_mood():
    cue = parse_scene_cue({"mood": ["bogus", "wonder"], "scene_id": "s1"})
    assert cue == MoodRequest("wonder", 0.5, "scene", "s1")
    assert parse_scene_cue({"mood": []}) is None


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_resolver_scene_dwell_then_override_wins_then_release():
    clock = Clock()
    r = MoodResolver(min_dwell_s=20, clock=clock)
    s1 = MoodRequest("tension", 0.5, "scene", "a")
    s2 = MoodRequest("sadness", 0.5, "scene", "b")
    assert r.resolve(None, s1) == s1           # first change is immediate
    clock.t = 5
    assert r.resolve(None, s2) == s1           # within dwell: held
    clock.t = 21
    assert r.resolve(None, s2) == s2           # dwell passed
    gm = MoodRequest("power", 0.9, "gm_override")
    clock.t = 22
    assert r.resolve(gm, s2) == gm             # override immediate
    clock.t = 23
    assert r.resolve(None, s2) == s2           # release -> back to scene


def test_scene_cue_file_round_trip(tmp_path):
    path = tmp_path / "cue.json"
    assert write_scene_cue(["tension"], "boss", 0.7, path=path)
    src = ControlSources("roundtable", cue_path=path)
    assert src.read_scene() == MoodRequest("tension", 0.7, "scene", "boss")


def test_override_read_degrades_when_redis_errors():
    class Boom:
        def get(self, key):
            raise ConnectionError("down")
    assert ControlSources("rt", redis_client=Boom()).read_override() is None


# ── recorder / store ───────────────────────────────────────────────────────

class FakeStore:
    def __init__(self, fail=False):
        self.segments, self.themes, self.ended, self.fail = [], [], [], fail

    def ensure_schema(self):
        pass

    def upsert_theme(self, theme, campaign=""):
        self.themes.append(theme)

    def start_session(self, theme, version, sr, **meta):
        return 42

    def end_session(self, sid):
        self.ended.append(sid)

    def save_segment(self, row):
        if self.fail:
            raise RuntimeError("db down")
        self.segments.append(row)


def test_recorder_splits_segments_on_mood_change_and_writes_all(theme):
    store = FakeStore()
    eng = MusicEngine(theme, mood="peacefulness")
    rec = MusicRecorder(store, eng, audio_format="wav")
    assert rec.start() == 42
    rec.set_context("scene", "a")
    for _ in range(3):
        rec.add_bar(eng.render_bar())
    eng.set_mood("tension")
    rec.set_context("gm_override")
    for _ in range(2):
        rec.add_bar(eng.render_bar())
    rec.close()
    assert [s["mood"] for s in store.segments] == ["peacefulness", "tension"]
    assert [s["bar_count"] for s in store.segments] == [3, 2]
    assert store.segments[1]["source"] == "gm_override"
    assert store.segments[0]["audio"][:4] == b"RIFF"
    assert all(s["notes"] and s["params"] for s in store.segments)
    json.dumps(store.segments[0]["notes"])  # JSON-safe
    assert store.ended == [42]


def test_recorder_db_failure_never_raises(theme):
    store = FakeStore(fail=True)
    eng = MusicEngine(theme)
    rec = MusicRecorder(store, eng, audio_format="wav")
    rec.start()
    rec.add_bar(eng.render_bar())
    rec.close()
    assert rec.errors == 1 and rec.saved == 0


def test_recorder_caps_segment_length(theme):
    store = FakeStore()
    eng = MusicEngine(theme, mood="joyful_activation")
    rec = MusicRecorder(store, eng, audio_format="wav", record_audio=False, max_segment_s=5)
    rec.start()
    for _ in range(6):
        rec.add_bar(eng.render_bar())
    rec.close()
    assert len(store.segments) > 1
    assert all(s["audio"] is None for s in store.segments)


def test_encode_audio_wav_fallback():
    data, fmt = encode_audio(np.zeros((100, 2), np.float32), 44100, fmt="wav")
    assert fmt == "wav" and data[:4] == b"RIFF"


class FakeCursor:
    def __init__(self):
        self.calls, self.rowcount = [], 1

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return (7,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self):
        self.cur, self.closed = FakeCursor(), False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def test_store_start_session_and_save_segment_sql(monkeypatch, theme):
    conns = []

    def fake_connect():
        conns.append(FakeConn())
        return conns[-1]
    monkeypatch.setattr(music_store, "_connect", fake_connect)
    sid = music_store.start_session(theme_to_dict(theme), engine_mod.ENGINE_VERSION, 44100,
                                    worker_id="roundtable")
    assert sid == 7
    assert music_store.save_segment({
        "session_id": 7, "seq": 0, "mood": "tension", "intensity": 0.5, "source": "scene",
        "scene_id": "", "bar_start": 0, "bar_count": 1, "start_s": 0.0, "duration_s": 2.0,
        "params": [{"a": 1}], "notes": [{"midi": 60}], "audio": b"xx", "audio_format": "wav",
    }) is True
    sql, params = conns[-1].cur.calls[0]
    assert "INSERT INTO music_segments" in sql
    assert json.loads(params["notes"]) == [{"midi": 60}]
    assert all(c.closed for c in conns)


def test_store_available_false_without_env(monkeypatch):
    for k in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert music_store.available() is False


# ── music_director: theme resolution + config gate ────────────────────────────
def test_resolve_theme_path_finds_campaign_theme(tmp_path):
    import music_director as md
    (tmp_path / "ashiorid" / "music").mkdir(parents=True)
    (tmp_path / "ashiorid" / "music" / "theme.yaml").write_text("x: 1")
    assert md.resolve_theme_path(tmp_path, "ashiorid") == tmp_path / "ashiorid" / "music" / "theme.yaml"


@pytest.mark.parametrize("name", ["", "../etc", "a/b", "missing", "x" * 65])
def test_resolve_theme_path_rejects_unsafe_or_missing(tmp_path, name):
    import music_director as md
    assert md.resolve_theme_path(tmp_path, name) is None


@pytest.mark.parametrize("config,env,expected", [
    ({}, None, False),
    ({"music": {"enabled": True}}, None, True),
    ({"music": {"enabled": True}}, "0", False),
    ({"music": {"enabled": False}}, "true", True),
])
def test_music_config_enabled_precedence(monkeypatch, config, env, expected):
    import music_director as md
    if env is None:
        monkeypatch.delenv("MUSIC_ENABLED", raising=False)
    else:
        monkeypatch.setenv("MUSIC_ENABLED", env)
    assert md.music_config(config)["enabled"] is expected


def test_print_enabled_reads_roundtable_config(monkeypatch, capsys):
    import music_director as md
    from pathlib import Path
    monkeypatch.delenv("MUSIC_ENABLED", raising=False)
    cfg = Path(__file__).resolve().parents[1] / "config" / "workers" / "roundtable.yaml"
    assert md.main(["--config", str(cfg), "--print-enabled"]) == 0
    assert capsys.readouterr().out.strip() == "1"


def test_scene_cue_theme_round_trips(tmp_path):
    from music.control import write_scene_cue, ControlSources
    cue = tmp_path / "cue.json"
    write_scene_cue("sadness", scene_id="s3", intensity=0.7, path=cue, theme="ashiorid")
    req = ControlSources("roundtable", cue_path=cue).read_scene()
    assert (req.mood, req.scene_id, req.theme) == ("sadness", "s3", "ashiorid")


def test_resolver_holds_configured_initial_mood_until_first_cue():
    from music.control import MoodResolver
    r = MoodResolver(initial_mood="peacefulness", clock=lambda: 0.0)
    req = r.resolve(None, None)
    assert (req.mood, req.source) == ("peacefulness", "hold")
