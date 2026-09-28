"""Tests for app/office/playlist.py (OB-33)."""
from datetime import datetime, timedelta, timezone

import pytest

from office import playlist as pl
from office.playlist import (
    REASON_OFF_HOURS,
    REASON_STALL,
    AmbientScene,
    OfficePlaylist,
    ReplayLibrary,
    load_ambient_pool,
    replay_request_message,
    replay_request_messages,
)

NOW = datetime(2026, 9, 27, 2, 0, tzinfo=timezone.utc)


class FakeLibrary:
    """fetch_json fake: returns `rows`, or raises when `fail` is set."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.fail = False

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        if self.fail:
            raise ConnectionError("down")
        return {"episodes": self.rows}


def row(name, status="approved"):
    return {"name": name, "status": status, "event_count": 10}


def write_scene(root, filename, scene_id, **extra):
    lines = [f"id: {scene_id}", f"title: {scene_id}"]
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    (root / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def scenes(tmp_path):
    write_scene(tmp_path, "a002-coffee-machine.yaml", "coffee-machine", ambient="true")
    write_scene(tmp_path, "a001-night-cleaner.yaml", "night-cleaner", ambient="true", phases="[off]")
    write_scene(tmp_path, "a003-lunch-queue.yaml", "lunch-queue", ambient="true", phases="[build]")
    write_scene(tmp_path, "00-first-standup.yaml", "first-standup")          # spine: not ambient pool
    write_scene(tmp_path, "a004-disabled.yaml", "disabled", ambient="false")
    (tmp_path / "a005-broken.yaml").write_text("id: [unterminated\n", encoding="utf-8")
    return tmp_path


def make(ambient_dir=None, rows=None, **kw):
    fetch = FakeLibrary(rows or [])
    library = ReplayLibrary("http://mapi:8090", fetch_json=fetch, refresh_s=0)
    if ambient_dir is None:
        return OfficePlaylist(ambient_pool=[], library=library, **kw), fetch
    return OfficePlaylist(scenes_dir=ambient_dir, library=library, **kw), fetch


def refs(playlist, n, reason=REASON_STALL, now=NOW):
    out = []
    for _ in range(n):
        item = playlist.next_item(now, reason)
        out.append(item.ref if item else None)
    return out


# ── ambient pool ──────────────────────────────────────────────────────────────

def test_load_ambient_pool_reads_only_a0nn_files_and_skips_bad(scenes):
    pool = load_ambient_pool(scenes)
    assert [s.scene_id for s in pool] == ["coffee-machine", "lunch-queue", "night-cleaner"]
    assert dict((s.scene_id, s.phases) for s in pool)["night-cleaner"] == ("off",)


def test_load_ambient_pool_missing_dir_is_empty(tmp_path):
    assert load_ambient_pool(tmp_path / "nope") == []


def test_default_pack_pool_loads_without_error():
    # The real pack has no a0NN scenes until OB-40; loading must still work.
    assert isinstance(load_ambient_pool(), list)


# ── ordering ──────────────────────────────────────────────────────────────────

def test_ambient_first_then_approved_replays_then_wraps(scenes):
    playlist, _ = make(scenes, rows=[row("office-claude_code-b"), row("office-claude_code-a")])
    assert refs(playlist, 6) == ["coffee-machine", "lunch-queue", "night-cleaner",
                                 "office-claude_code-a", "office-claude_code-b", "coffee-machine"]


def test_drafts_and_non_office_replays_excluded(scenes):
    playlist, fetch = make(ambient_dir=None, rows=[
        row("office-claude_code-ok"), row("office-claude_code-draft", status="draft"),
        row("ashiorid_generated_ce8d"), {"bad": "row"}, "junk"])
    assert refs(playlist, 3) == ["office-claude_code-ok"] * 3
    assert fetch.calls[0] == ("http://mapi:8090/replays", {"status": "approved"})


def test_no_immediate_repeats_over_many_picks(scenes):
    playlist, _ = make(scenes, rows=[row("office-x-1"), row("office-x-2")])
    picks = refs(playlist, 40)
    assert all(a != b for a, b in zip(picks, picks[1:]))
    assert set(picks) == {"coffee-machine", "lunch-queue", "night-cleaner", "office-x-1", "office-x-2"}


def test_rotation_is_deterministic_across_instances(scenes):
    a, _ = make(scenes, rows=[row("office-x-1")])
    b, _ = make(scenes, rows=[row("office-x-1")])
    assert refs(a, 7) == refs(b, 7)


def test_removed_item_continues_rotation_instead_of_restarting(scenes):
    playlist, fetch = make(ambient_dir=None, rows=[row("office-a"), row("office-b"), row("office-c")])
    assert refs(playlist, 2) == ["office-a", "office-b"]
    fetch.rows = [row("office-a"), row("office-c")]      # b rejected/deleted mid-rotation
    assert refs(playlist, 2) == ["office-c", "office-a"]


def test_off_hours_filters_phase_tagged_ambient(scenes):
    playlist, _ = make(scenes)
    # lunch-queue is tagged [build]: never offered off hours; untagged scenes always are.
    assert refs(playlist, 4, reason=REASON_OFF_HOURS) == [
        "coffee-machine", "night-cleaner", "coffee-machine", "night-cleaner"]
    build = [playlist.next_item(NOW, REASON_STALL, phase="build").ref for _ in range(2)]
    assert build == ["coffee-machine", "lunch-queue"]


# ── fallbacks ─────────────────────────────────────────────────────────────────

def test_empty_pool_returns_none():
    playlist, _ = make()
    assert playlist.next_item(NOW, REASON_OFF_HOURS) is None
    assert OfficePlaylist(ambient_pool=[], library=None).next_item(NOW, REASON_STALL) is None


def test_empty_ambient_falls_back_to_replays():
    playlist, _ = make(rows=[row("office-only")])
    item = playlist.next_item(NOW, REASON_OFF_HOURS)
    assert (item.kind, item.episode, item.reason, item.picked_at) == ("replay", "office-only", "off_hours", NOW)


def test_single_item_pool_repeats():
    playlist, _ = make(rows=[row("office-only")])
    assert refs(playlist, 3) == ["office-only"] * 3


def test_library_down_keeps_last_good_list():
    fetch = FakeLibrary([row("office-a"), row("office-b")])
    library = ReplayLibrary("http://mapi:8090", fetch_json=fetch, refresh_s=0)
    playlist = OfficePlaylist(ambient_pool=[], library=library)
    assert playlist.next_item(NOW, REASON_STALL).ref == "office-a"
    fetch.fail = True
    assert playlist.next_item(NOW, REASON_STALL).ref == "office-b"


def test_library_down_from_start_uses_ambient_only(scenes):
    fetch = FakeLibrary([])
    fetch.fail = True
    playlist = OfficePlaylist(scenes_dir=scenes, library=ReplayLibrary(fetch_json=fetch))
    assert playlist.next_item(NOW, REASON_STALL).kind == "ambient"


def test_library_cache_respects_refresh_window():
    fetch = FakeLibrary([row("office-a")])
    library = ReplayLibrary(fetch_json=fetch, refresh_s=300)
    library.approved(NOW)
    library.approved(NOW + timedelta(seconds=10))
    assert len(fetch.calls) == 1
    library.approved(NOW + timedelta(seconds=301))
    assert len(fetch.calls) == 2


def test_bad_reason_raises():
    with pytest.raises(ValueError):
        OfficePlaylist(ambient_pool=[]).next_item(NOW, "lunch")


def test_ambient_requires_approved_gates_on_library(scenes):
    playlist, fetch = make(scenes, rows=[row("office-ambient-coffee-machine")],
                           ambient_requires_approved=True)
    # Only the built+approved ambient scene is eligible, and it isn't queued twice.
    assert refs(playlist, 2) == ["coffee-machine", "coffee-machine"]
    assert playlist.last.episode == "office-ambient-coffee-machine"


def test_ambient_episode_not_duplicated_as_replay(scenes):
    playlist, _ = make(scenes, rows=[row("office-ambient-coffee-machine"), row("office-z")])
    kinds = [i.kind for i in playlist.candidates(NOW, REASON_STALL)]
    assert kinds == ["ambient", "ambient", "ambient", "replay"]


# ── message ───────────────────────────────────────────────────────────────────

def test_replay_request_message_shape():
    item = pl.PlaylistItem("replay", "office-a", "office-a", REASON_STALL, NOW)
    msg = replay_request_message(item, "worker1", cast={"tuber_0": "worker1"})
    assert msg["type"] == "replay_request" and msg["to"] == "worker1"
    assert msg["from"] == "office_playlist"
    assert msg["payload"] == {"episode": "office-a", "cast": {"tuber_0": "worker1"},
                              "playlist": {"kind": "replay", "reason": "stall"}}
    assert msg["correlation_id"] == msg["id"]


def test_replay_request_messages_fans_out_and_needs_recipient():
    item = pl.PlaylistItem("ambient", "s", "office-ambient-s", REASON_OFF_HOURS, NOW, "/x.yaml")
    msgs = replay_request_messages(item, ["w1", "w2"], correlation_id="c-1")
    assert [m["to"] for m in msgs] == ["w1", "w2"]
    assert all(m["correlation_id"] == "c-1" for m in msgs)
    with pytest.raises(ValueError):
        replay_request_message(item, "")


def test_ambient_scene_dataclass_default_phases():
    assert AmbientScene("x", pl.Path("x.yaml")).phases == ()


# ── day runner adapter (OB-30 contract) ───────────────────────────────────────

from zoneinfo import ZoneInfo  # noqa: E402

from office import day_runner as dr  # noqa: E402
from office.playlist import DayRunnerPlaylist, day_runner_playlist  # noqa: E402

NY = ZoneInfo("America/New_York")


class ListProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message


def ny(day, hh, mm=0):
    return datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}").replace(tzinfo=NY)


def ctx(reason, **extra):
    return {"worker_id": "tuber_0", "day": "2026-09-28", "reason": reason, "directives": [], **extra}


def test_adapter_maps_item_to_day_runner_request():
    playlist, _ = make(rows=[row("office-a"), row("office-b")])
    adapter = DayRunnerPlaylist(playlist, clock=lambda: NOW, speed=1.25,
                                cast={"tuber_0": "roundtable"}, worker_name="Ashiorid")
    assert adapter.off_hours("2026-09-28", ctx("off_hours")) == {
        "episode": "office-a", "speed": 1.25, "cast": {"tuber_0": "roundtable"},
        "worker_name": "Ashiorid"}
    assert adapter.stall("2026-09-28", ctx("stall", idle_minutes=50))["episode"] == "office-b"
    assert adapter.playlist.last.reason == REASON_STALL


def test_adapter_returns_none_when_nothing_to_air():
    playlist, _ = make()
    adapter = DayRunnerPlaylist(playlist, clock=lambda: NOW)
    assert adapter.off_hours("2026-09-28", ctx("off_hours")) is None
    assert adapter.stall("2026-09-28", ctx("stall")) is None
    assert adapter.resume_live("2026-09-28", ctx("day_start")) is None


def test_adapter_forces_ambient_approved_only(scenes):
    # An unbuilt ambient scene has no library episode: it must never be requested.
    playlist, _ = make(scenes, rows=[row("office-ambient-night-cleaner"), row("office-r")])
    adapter = DayRunnerPlaylist(playlist, clock=lambda: NOW)
    assert playlist.ambient_requires_approved is True
    picks = [adapter.off_hours("d", ctx("off_hours"))["episode"] for _ in range(3)]
    assert picks == ["office-ambient-night-cleaner", "office-r", "office-ambient-night-cleaner"]


def test_adapter_stall_filters_by_office_phase_of_now(scenes):
    playlist, _ = make(scenes, rows=[row("office-ambient-lunch-queue"),
                                     row("office-ambient-night-cleaner")])
    # 13:00 New York is the build phase: lunch-queue [build] airs, night-cleaner [off] doesn't.
    adapter = DayRunnerPlaylist(playlist, clock=lambda: ny("2026-09-28", 13))
    assert [adapter.stall("d", ctx("stall"))["episode"] for _ in range(2)] == [
        "office-ambient-lunch-queue"] * 2


def test_adapter_bad_tz_raises():
    with pytest.raises(ValueError):
        DayRunnerPlaylist(OfficePlaylist(ambient_pool=[]), tz="Mars/Olympus")


@pytest.mark.parametrize("opts, env, expected_url", [
    ({"message_api_url": "http://cfg:1"}, "http://env:2", "http://cfg:1/replays"),
    ({}, "http://env:2", "http://env:2/replays"),
    ({}, None, f"{pl.DEFAULT_MESSAGE_API_URL}/replays"),
])
def test_factory_message_api_url_precedence(monkeypatch, tmp_path, opts, env, expected_url):
    if env is None:
        monkeypatch.delenv("MESSAGE_API_URL", raising=False)
    else:
        monkeypatch.setenv("MESSAGE_API_URL", env)
    opts = dict(opts, scenes_dir=str(tmp_path))
    adapter = day_runner_playlist({"office": {"day_runner": {"playlist_options": opts}}})
    assert isinstance(adapter, DayRunnerPlaylist)
    assert adapter.playlist.library.url == expected_url
    assert adapter.playlist.ambient_requires_approved is True


def test_factory_scenes_dir_from_office_pack_dir_and_options(monkeypatch, tmp_path, scenes):
    monkeypatch.setenv("OFFICE_PACK_DIR", str(tmp_path / "pack"))
    adapter = day_runner_playlist({"office": {"day_runner": {}}})
    assert pl.Path(adapter.playlist.scenes_dir) == tmp_path / "pack" / "scenes"
    adapter = day_runner_playlist({"office": {"tz": "UTC", "day_runner": {
        "playlist_options": {"scenes_dir": str(scenes), "library": False, "speed": 2}}}})
    assert adapter.playlist.library is None and adapter.speed == 2 and adapter.tz == "UTC"
    assert len(adapter.playlist.ambient_pool) == 3


def test_factory_tolerates_missing_config(monkeypatch, tmp_path):
    monkeypatch.delenv("OFFICE_PACK_DIR", raising=False)
    adapter = day_runner_playlist(None)
    assert adapter.playlist.scenes_dir == pl.DEFAULT_SCENES_DIR
    adapter = day_runner_playlist({"office": {"day_runner": {"playlist_options": "junk"}}})
    assert adapter.playlist.library is not None


def test_day_runner_with_real_adapter_hands_off_at_midnight(monkeypatch, tmp_path):
    """Integration: config-built DayRunner + day_runner_playlist + a fake
    library; the 00:00 s0 hand-off sends a replay_request for an approved
    office replay (never the draft)."""
    fetch = FakeLibrary([row("office-claude_code-sess-001"),
                         row("office-claude_code-draft", status="draft")])
    monkeypatch.setattr(pl, "_httpx_get_json", fetch)
    monkeypatch.setenv("MESSAGE_API_URL", "http://message-api:8000")
    clock = [ny("2026-09-29", 0, 0)]
    cfg = {"role": "ceo", "office_role": "ceo", "office": {"day_runner": {
        "state_path": str(tmp_path / "day_runner.json"),
        "playlist": "office.playlist:day_runner_playlist",
        "playlist_options": {"scenes_dir": str(tmp_path / "no-scenes")},
        "backlog": False}}}
    runner = dr.build_day_runner(cfg, worker_id="tuber_0", clock=lambda: clock[0])
    assert isinstance(runner.playlist, DayRunnerPlaylist)
    producer = ListProducer()

    sent = runner.tick("tuber_0", cfg, None, producer)
    assert [m["type"] for m in sent] == ["phase_change", "replay_request"]
    req = sent[1]
    assert req["to"] == "tuber_0" and req["from"] == "tuber_0"
    assert req["payload"] == {"episode": "office-claude_code-sess-001", "reason": "off_hours",
                              "day": "2026-09-29"}
    assert fetch.calls[0] == ("http://message-api:8000/replays", {"status": "approved"})
    assert producer.sent == sent

    # Later in s0: no second hand-off, also after a restart from the state file.
    clock[0] = ny("2026-09-29", 3, 0)
    assert runner.tick("tuber_0", cfg, None, producer) == []
    again = dr.build_day_runner(cfg, worker_id="tuber_0", clock=lambda: clock[0])
    assert again.tick("tuber_0", cfg, None, producer) == []
