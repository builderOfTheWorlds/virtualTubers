"""Tests for app/office/day_runner.py (OB-30): the CEO-as-GM day runner.

Everything external is faked: the clock (a mutable fake), the LLM
(ScriptedLLM), Gitea (FakeGitea via agent_handlers.office.build_gitea_client),
the playlist and the directive sources. Character files live in tmp_path.
"""
import json
import sys
import types
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import yaml

from agent_handlers import office
from agent_handlers.manager import MAX_BUG_RETRIES
from e2e_harness import ScriptedLLM, llm_reply
from office import day_runner as dr
from office.protocol import validate_message
from office.roles import SEAT, OfficeRole as R

NY = ZoneInfo("America/New_York")
CEO = SEAT[R.CEO]
DAY = "2026-09-28"          # a Monday
NEXT = "2026-09-29"


# ── fakes ────────────────────────────────────────────────────────────────────
class FakeClock:
    def __init__(self, day=DAY, hh=6, mm=0):
        self.now = datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}").replace(tzinfo=NY)

    def set(self, day, hh, mm=0):
        self.now = datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}").replace(tzinfo=NY)

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)

    def __call__(self):
        return self.now


class FakeGitea:
    def __init__(self, issues=()):
        self.issues = {i["number"]: dict(i, state="open") for i in issues}
        self.calls = []
        self._next = 100

    def open_issue(self, title, body="", labels=None):
        self._next += 1
        self.calls.append(("open_issue", title))
        self.issues[self._next] = {"number": self._next, "title": title, "body": body,
                                   "state": "open",
                                   "labels": [{"name": n} for n in labels or []]}
        return {"number": self._next}

    def list_issues(self, state="open", labels=None):
        self.calls.append(("list_issues", state, tuple(labels or ())))
        out = [i for i in self.issues.values() if i["state"] == state]
        if labels:
            out = [i for i in out if set(labels) <= {lb["name"] for lb in i.get("labels", [])}]
        return out

    def comment_issue(self, number, body):
        self.calls.append(("comment_issue", number, body))
        return {"id": 1}

    def close(self, number):
        self.issues[number]["state"] = "closed"


class ListProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message

    def types(self):
        return [m["type"] for m in self.sent]

    def clear(self):
        self.sent.clear()


class FakePlaylist:
    def __init__(self, off=None, stall=None):
        self.calls = []
        self._off = off
        self._stall = stall

    def off_hours(self, day, context):
        self.calls.append(("off_hours", day, context["reason"]))
        return self._off

    def stall(self, day, context):
        self.calls.append(("stall", day, context["idle_minutes"]))
        return self._stall

    def resume_live(self, day, context):
        self.calls.append(("resume_live", day))


def arc(text="Ship the velocity rule.", title="Velocity rule"):
    calls = []

    def provider(day, context):
        calls.append((day, context["kind"], context["index"]))
        return {"text": text, "title": title, "ref": f"arc:{day}:{context['index']}"}
    provider.calls = calls
    return provider


def feature_record(sid="s1", tags=("feature",), text="Add a blocklist for merchant ids."):
    return {"source_tool": "claude_code", "session_id": sid, "project": "x",
            "started_at": "2026-09-20T10:00:00", "tags": list(tags),
            "events": [{"seq": 0, "type": "user_message", "text": text},
                       {"seq": 1, "type": "assistant_text", "text": "I'll plan it first."}]}


# ── fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture
def pack(tmp_path):
    root = tmp_path / "pack"
    for role in R:
        (root / "cast").mkdir(parents=True, exist_ok=True)
        (root / "profiles").mkdir(parents=True, exist_ok=True)
        (root / "cast" / f"{role.value}.yaml").write_text(
            yaml.safe_dump({"name": role.value, "system_prompt": f"You are the {role.value}."}),
            encoding="utf-8")
        (root / "profiles" / f"{role.value}.yaml").write_text(
            yaml.safe_dump({"id": role.value, "backstory": {"believed": f"I am {role.value}."}}),
            encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def clean_office():
    office._reset_office_state()
    yield
    office._reset_office_state()


@pytest.fixture
def gitea(monkeypatch):
    fake = FakeGitea()
    monkeypatch.setattr(office, "build_gitea_client", lambda cfg: fake)
    return fake


def ceo_cfg(pack, **runner_cfg):
    cfg = {"role": "ceo", "office_role": "ceo", "system_prompt": "CONFIG PROMPT ceo",
           "office": {"pack_dir": str(pack)}}
    if runner_cfg:
        cfg["office"]["day_runner"] = runner_cfg
    return cfg


def make_runner(tmp_path, clock, **kw):
    kw.setdefault("state_path", str(tmp_path / "day_runner.json"))
    kw.setdefault("stall_minutes", 10_000)
    kw.setdefault("completion_poll_s", 0)
    kw.setdefault("retry_backoff_s", 0)
    return dr.DayRunner(clock=clock, **kw)


def tick(runner, pack, producer, llm=None):
    return runner(CEO, ceo_cfg(pack), llm or ScriptedLLM("ceo"), producer, None)


def directives(msgs):
    return [m for m in msgs if m["type"] == "directive"]


# ── full simulated day ───────────────────────────────────────────────────────
def test_full_day_runs_start_directive_phases_wrap_up_day_end_and_playlist(tmp_path, pack, gitea):
    clock, producer = FakeClock(DAY, 6, 0), ListProducer()
    playlist = FakePlaylist(off={"episode": "office-ambient-night", "speed": 1.0})
    runner = make_runner(tmp_path, clock, arc_provider=arc(), playlist=playlist)

    # 06:00 — day_start, phase_change(morning), then the CEO's directive (4 recipients).
    sent = tick(runner, pack, producer)
    assert [m["type"] for m in sent][:2] == ["day_start", "phase_change"]
    assert sent[0]["payload"] == {"day": DAY} and sent[0]["from"] == CEO
    assert sent[1]["payload"]["phase"] == "morning" and sent[1]["payload"]["segment"] == 1
    assert sent[1]["correlation_id"] == sent[0]["correlation_id"]
    ds = directives(sent)
    assert len(ds) == 4 and ds[0]["payload"]["text"] == "Ship the velocity rule."
    assert ds[0]["payload"]["issue"] == 101 and ds[0]["payload"]["day"] == DAY
    assert ("resume_live", DAY) in playlist.calls
    for msg in sent:
        validate_message(msg)

    # Same minute, and later in s1: nothing new.
    assert tick(runner, pack, producer) == []
    clock.set(DAY, 11, 59)
    assert tick(runner, pack, producer) == []

    # Segment edges.
    clock.set(DAY, 12, 0)
    sent = tick(runner, pack, producer)
    assert [(m["type"], m["payload"]["phase"], m["payload"]["previous"]) for m in sent] == [
        ("phase_change", "build", "morning")]
    clock.set(DAY, 18, 0)
    sent = tick(runner, pack, producer)
    assert [(m["type"], m["payload"]["phase"]) for m in sent] == [("phase_change", "ship")]

    # 23:45 wrap-up: wrap_up broadcast + a carry-over comment on the open issue.
    clock.set(DAY, 23, 45)
    sent = tick(runner, pack, producer)
    assert [m["type"] for m in sent] == ["wrap_up"]
    assert sent[0]["to"] == "broadcast" and sent[0]["payload"]["request"] == "status_report"
    assert sent[0]["payload"]["directives"][0]["status"] == "active"
    assert any(c[0] == "comment_issue" and c[1] == 101 for c in gitea.calls)
    clock.set(DAY, 23, 59)
    assert tick(runner, pack, producer) == []

    # 00:00 — day_end(yesterday), phase_change(off), s0 hand-off to the playlist.
    clock.set(NEXT, 0, 0)
    sent = tick(runner, pack, producer)
    assert [m["type"] for m in sent] == ["day_end", "phase_change", "replay_request"]
    assert sent[0]["payload"]["day"] == DAY and "1 directive(s)" in sent[0]["payload"]["summary"]
    assert sent[1]["payload"] == {"phase": "off", "day": NEXT, "previous": "ship", "segment": 0}
    assert sent[2]["payload"] == {"episode": "office-ambient-night", "reason": "off_hours",
                                  "day": NEXT, "speed": 1.0}
    assert sent[2]["to"] == CEO
    assert ("off_hours", NEXT, "off_hours") in playlist.calls
    for msg in sent[:2]:
        validate_message(msg)

    # During s0: no live work, no second hand-off.
    clock.set(NEXT, 3, 0)
    assert tick(runner, pack, producer) == []

    # 06:00 the next day starts a new day with a fresh directive.
    clock.set(NEXT, 6, 0)
    sent = tick(runner, pack, producer)
    assert [m["type"] for m in sent][:2] == ["day_start", "phase_change"]
    assert sent[1]["payload"]["previous"] == "off"
    assert len(directives(sent)) == 4 and directives(sent)[0]["payload"]["day"] == NEXT


def test_off_hours_without_playlist_logs_and_sends_nothing(tmp_path, pack, gitea, capsys):
    clock, producer = FakeClock(DAY, 2, 0), ListProducer()
    runner = make_runner(tmp_path, clock)
    sent = tick(runner, pack, producer)
    assert [m["type"] for m in sent] == ["phase_change"]
    assert "off_hours_no_playlist" in capsys.readouterr().out
    assert tick(runner, pack, producer) == []


def test_ceo_worker_id_not_a_seat_sends_as_office_clock(tmp_path, pack, gitea):
    clock, producer = FakeClock(DAY, 13, 0), ListProducer()
    runner = make_runner(tmp_path, clock, backlog_source=None)
    sent = runner("ceo", ceo_cfg(pack), ScriptedLLM("ceo"), producer, None)
    assert sent[0]["from"] == "office_clock"
    assert sent[1]["payload"]["phase"] == "build"      # late start lands in the right phase


# ── directive sources and their fallback ─────────────────────────────────────
def test_arc_provider_wins_over_feature_and_backlog(tmp_path, pack, gitea):
    feature = lambda day, ctx: pytest.fail("feature source must not be asked")  # noqa: E731
    runner = make_runner(tmp_path, FakeClock(), arc_provider=arc(), feature_source=feature,
                         backlog_source=dr.GiteaIssueBacklog())
    tick(runner, pack, ListProducer())
    assert runner.state["directives"][0]["source"] == "arc"


def test_feature_session_rewritten_when_no_arc(tmp_path, pack, gitea):
    source = dr.CorpusFeatureSource(loader=lambda: [feature_record("old", tags=("bugfix",)),
                                                    feature_record("s1")])
    llm = ScriptedLLM("ceo", replies=[
        json.dumps({"title": "Merchant blocklist", "text": "Add a merchant blocklist to scoring."}),
    ])
    runner = make_runner(tmp_path, FakeClock(), feature_source=source,
                         backlog_source=dr.GiteaIssueBacklog())
    producer = ListProducer()
    tick(runner, pack, producer, llm)
    ds = directives(producer.sent)
    assert ds and ds[0]["payload"]["text"] == "Add a merchant blocklist to scoring."
    assert ds[0]["payload"]["title"] == "Merchant blocklist"
    record = runner.state["directives"][0]
    assert record["source"] == "feature" and record["ref"] == "corpus:s1"
    assert "corpus:s1" in runner.state["used_refs"]
    assert "Session outline" in llm.prompts[0] and "merchant ids" in llm.prompts[0]


def test_feature_llm_failure_falls_back_to_backlog(tmp_path, pack, gitea):
    gitea.issues[7] = {"number": 7, "title": "Rate-limit the verdict API", "body": "Per bank.",
                       "state": "open", "labels": []}
    source = dr.CorpusFeatureSource(loader=lambda: [feature_record()])
    llm = ScriptedLLM("ceo", fail_on=("Session outline",))
    runner = make_runner(tmp_path, FakeClock(), arc_provider=lambda d, c: None,
                         feature_source=source, backlog_source=dr.GiteaIssueBacklog())
    producer = ListProducer()
    tick(runner, pack, producer, llm)
    record = runner.state["directives"][0]
    assert record["source"] == "backlog" and record["ref"] == "issue:7"
    text = directives(producer.sent)[0]["payload"]["text"]
    assert text.startswith("Rate-limit the verdict API. Per bank.") and "#7" in text


def test_feature_rewrite_failing_leak_audit_is_refused(tmp_path, pack, gitea):
    source = dr.CorpusFeatureSource(loader=lambda: [feature_record()])
    llm = ScriptedLLM("ceo", replies=[json.dumps({"title": "x", "text": "Use password: hunter2"})])
    assert source(DAY, {"llm_client": llm, "persona": "p", "used_refs": set()}) is None


def test_feature_source_skips_used_sessions_and_needs_llm():
    source = dr.CorpusFeatureSource(loader=lambda: [feature_record("s1")])
    assert source(DAY, {"llm_client": None}) is None
    llm = ScriptedLLM("ceo", replies=["Build the merchant blocklist today."])
    assert source(DAY, {"llm_client": llm, "used_refs": {"corpus:s1"}}) is None
    picked = source(DAY, {"llm_client": llm, "used_refs": set()})
    assert picked["text"] == "Build the merchant blocklist today." and picked["title"] is None


def test_feature_source_reads_jsonl_export(tmp_path):
    export = tmp_path / "export.jsonl"
    export.write_text("\n".join([json.dumps(feature_record("a")), "not json", ""]),
                      encoding="utf-8")
    assert [r["session_id"] for r in dr.CorpusFeatureSource(str(export))._load_export()] == ["a"]
    assert dr.CorpusFeatureSource(str(tmp_path / "missing.jsonl"))._load_export() == []


def test_backlog_skips_directive_issues_and_used_refs():
    fake = FakeGitea([
        {"number": 3, "title": "Old directive", "labels": [{"name": "directive"}]},
        {"number": 5, "title": "Used yesterday", "labels": []},
        {"number": 9, "title": "Add chargeback feed", "body": "", "labels": []},
    ])
    picked = dr.GiteaIssueBacklog()(DAY, {"gitea": fake, "used_refs": {"issue:5"}})
    assert picked == {"text": "Add chargeback feed (backlog issue #9)",
                      "title": "Add chargeback feed", "ref": "issue:9"}
    assert dr.GiteaIssueBacklog()(DAY, {"gitea": None}) is None


def test_raising_source_is_skipped(tmp_path, pack, gitea):
    def boom(day, ctx):
        raise RuntimeError("arc db down")
    runner = make_runner(tmp_path, FakeClock(), arc_provider=boom,
                         backlog_source=lambda d, c: {"text": "Backlog thing", "ref": "issue:1"})
    tick(runner, pack, ListProducer())
    assert runner.state["directives"][0]["source"] == "backlog"


# ── guards ───────────────────────────────────────────────────────────────────
def test_follow_ups_only_after_done_and_capped_at_two(tmp_path, pack, gitea):
    clock, producer = FakeClock(DAY, 6, 0), ListProducer()
    provider = arc()
    runner = make_runner(tmp_path, clock, arc_provider=provider)
    tick(runner, pack, producer)
    assert len(runner.state["directives"]) == 1

    clock.advance(30)
    tick(runner, pack, producer)          # still open -> no follow-up
    assert len(runner.state["directives"]) == 1

    for expected in (2, 3, 3, 3):
        open_issue = runner.state["directives"][-1]["issue"]
        gitea.close(open_issue)
        clock.advance(30)
        tick(runner, pack, producer)
        assert len(runner.state["directives"]) == expected
    kinds = [d["kind"] for d in runner.state["directives"]]
    assert kinds == ["primary", "follow_up", "follow_up"]
    assert [c[1] for c in provider.calls] == ["primary", "follow_up", "follow_up"]
    assert all(d["status"] == "done" for d in runner.state["directives"])


def test_max_follow_ups_config_can_lower_but_not_raise_cap(tmp_path):
    assert make_runner(tmp_path, FakeClock(), max_follow_ups=5).max_follow_ups == 2
    assert make_runner(tmp_path, FakeClock(), max_follow_ups=0).max_follow_ups == 0


def test_retry_cap_stops_after_max_bug_retries_with_backoff(tmp_path, pack, gitea, capsys):
    calls = []

    def failing_issue(*args, **kwargs):
        calls.append(1)
        return []
    clock, producer = FakeClock(DAY, 6, 0), ListProducer()
    runner = make_runner(tmp_path, clock, arc_provider=arc(), issue_directive=failing_issue,
                         retry_backoff_s=300)
    tick(runner, pack, producer)
    assert len(calls) == 1 and runner.state["attempts"] == 1
    clock.advance(2)
    tick(runner, pack, producer)          # inside the backoff window
    assert len(calls) == 1
    for _ in range(10):
        clock.advance(6)
        tick(runner, pack, producer)
    assert len(calls) == MAX_BUG_RETRIES == dr.MAX_DIRECTIVE_ATTEMPTS
    assert runner.state["exhausted"] and runner.state["directives"] == []
    assert "directive_retries_exhausted" in capsys.readouterr().out


def test_empty_sources_count_as_failed_attempts(tmp_path, pack, gitea):
    clock = FakeClock(DAY, 6, 0)
    runner = make_runner(tmp_path, clock, backlog_source=dr.GiteaIssueBacklog())
    for _ in range(MAX_BUG_RETRIES + 2):
        tick(runner, pack, ListProducer())
        clock.advance(1)
    assert runner.state["attempts"] == MAX_BUG_RETRIES and runner.state["exhausted"]


def test_issue_directive_exception_is_a_failed_attempt(tmp_path, pack, gitea):
    def raising(*a, **k):
        raise RuntimeError("bus down")
    runner = make_runner(tmp_path, FakeClock(), arc_provider=arc(), issue_directive=raising)
    assert directives(tick(runner, pack, ListProducer())) == []
    assert runner.state["attempts"] == 1 and runner.state["directives"] == []


# ── stall detector ───────────────────────────────────────────────────────────
def test_stall_emits_fallback_replay_after_idle_minutes_and_rearms(tmp_path, pack, gitea):
    clock, producer = FakeClock(DAY, 7, 0), ListProducer()
    playlist = FakePlaylist(stall={"episode": "office-replay-042"})
    runner = make_runner(tmp_path, clock, arc_provider=arc(), playlist=playlist,
                         stall_minutes=30, completion_poll_s=10_000)
    tick(runner, pack, producer)
    producer.clear()

    clock.advance(29)
    assert tick(runner, pack, producer) == []
    clock.advance(2)
    sent = tick(runner, pack, producer)
    assert [m["type"] for m in sent] == ["replay_request"]
    assert sent[0]["payload"]["episode"] == "office-replay-042"
    assert sent[0]["payload"]["reason"] == "stall"
    assert playlist.calls[-1][0] == "stall" and playlist.calls[-1][2] == pytest.approx(31, abs=0.1)

    clock.advance(5)
    assert tick(runner, pack, producer) == []           # re-armed, not repeated
    clock.advance(26)
    assert [m["type"] for m in tick(runner, pack, producer)] == ["replay_request"]


def test_activity_probe_postpones_stall(tmp_path, pack, gitea):
    clock, producer = FakeClock(DAY, 7, 0), ListProducer()
    playlist = FakePlaylist(stall={"episode": "x"})
    runner = make_runner(tmp_path, clock, playlist=playlist, stall_minutes=30,
                         activity_probe=lambda: (clock() - timedelta(minutes=1)).timestamp())
    for _ in range(4):
        clock.advance(20)
        tick(runner, pack, producer)
    assert not any(c[0] == "stall" for c in playlist.calls)


def test_stall_playlist_returning_none_sends_nothing(tmp_path, pack, gitea):
    clock, producer = FakeClock(DAY, 7, 0), ListProducer()
    playlist = FakePlaylist(stall=None)
    runner = make_runner(tmp_path, clock, playlist=playlist, stall_minutes=10)
    tick(runner, pack, producer)
    producer.clear()
    clock.advance(11)
    assert tick(runner, pack, producer) == []
    assert playlist.calls[-1][0] == "stall"


def test_no_stall_after_wrap_up(tmp_path, pack, gitea):
    clock, producer = FakeClock(DAY, 23, 0), ListProducer()
    playlist = FakePlaylist(stall={"episode": "x"})
    runner = make_runner(tmp_path, clock, playlist=playlist, stall_minutes=10)
    tick(runner, pack, producer)
    clock.set(DAY, 23, 46)
    tick(runner, pack, producer)
    clock.set(DAY, 23, 59)
    tick(runner, pack, producer)
    assert not any(c[0] == "stall" for c in playlist.calls)


def test_mtime_probe_returns_newest(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("1")
    b.write_text("2")
    import os
    os.utime(a, (100, 100))
    os.utime(b, (200, 200))
    assert dr.mtime_probe([str(a), str(b), str(tmp_path / "nope")])() == 200
    assert dr.mtime_probe([])() is None


# ── restart idempotency ──────────────────────────────────────────────────────
def test_restart_mid_day_does_not_repeat_day_start_or_directive(tmp_path, pack, gitea):
    clock, producer = FakeClock(DAY, 6, 0), ListProducer()
    first = make_runner(tmp_path, clock, arc_provider=arc())
    tick(first, pack, producer)
    assert producer.types().count("day_start") == 1 and len(directives(producer.sent)) == 4

    clock.set(DAY, 9, 30)
    second = make_runner(tmp_path, clock, arc_provider=arc())
    assert tick(second, pack, producer) == []
    assert second.state["directives"][0]["issue"] == 101

    clock.set(DAY, 23, 50)
    third = make_runner(tmp_path, clock, arc_provider=arc())
    assert [m["type"] for m in tick(third, pack, producer)] == ["phase_change", "wrap_up"]
    fourth = make_runner(tmp_path, clock, arc_provider=arc())
    assert tick(fourth, pack, producer) == []


def test_crash_while_issuing_never_issues_a_second_directive(tmp_path, pack, gitea):
    clock = FakeClock(DAY, 6, 0)

    def crash(*args, **kwargs):
        raise KeyboardInterrupt  # not caught by the runner: simulates the process dying
    first = make_runner(tmp_path, clock, arc_provider=arc(), issue_directive=crash)
    with pytest.raises(KeyboardInterrupt):
        first.tick(CEO, ceo_cfg(pack), ScriptedLLM("ceo"), ListProducer())
    saved = json.loads((tmp_path / "day_runner.json").read_text())
    assert saved["directives"][0]["status"] == "issuing"

    clock.advance(5)
    second = make_runner(tmp_path, clock, arc_provider=arc())
    producer = ListProducer()
    tick(second, pack, producer)
    assert directives(producer.sent) == [] and "day_start" not in producer.types()


def test_restart_after_missed_night_closes_old_day_late(tmp_path, pack, gitea):
    clock, producer = FakeClock(DAY, 20, 0), ListProducer()
    tick(make_runner(tmp_path, clock, arc_provider=arc()), pack, producer)
    producer.clear()
    clock.set(NEXT, 7, 0)
    runner = make_runner(tmp_path, clock, arc_provider=arc())
    sent = tick(runner, pack, producer)
    types_ = [m["type"] for m in sent]
    assert types_[:4] == ["wrap_up", "day_end", "day_start", "phase_change"]
    assert sent[1]["payload"]["day"] == DAY and sent[2]["payload"]["day"] == NEXT
    assert len(directives(sent)) == 4


def test_restart_in_s0_after_down_at_midnight_ends_day_once(tmp_path, pack, gitea):
    clock, producer = FakeClock(DAY, 22, 0), ListProducer()
    tick(make_runner(tmp_path, clock, arc_provider=arc()), pack, producer)
    clock.set(NEXT, 0, 20)
    playlist = FakePlaylist()
    runner = make_runner(tmp_path, clock, playlist=playlist)
    sent = tick(runner, pack, producer)
    assert [m["type"] for m in sent] == ["wrap_up", "day_end", "phase_change"]
    again = make_runner(tmp_path, clock, playlist=playlist)
    assert tick(again, pack, producer) == []
    assert [c[0] for c in playlist.calls] == ["off_hours"]


def test_corrupt_or_old_state_file_starts_fresh(tmp_path, pack, gitea):
    path = tmp_path / "day_runner.json"
    path.write_text("{not json", encoding="utf-8")
    runner = make_runner(tmp_path, FakeClock(DAY, 2, 0))
    assert runner.state["day"] is None
    path.write_text(json.dumps({"version": 99, "day": DAY}), encoding="utf-8")
    assert make_runner(tmp_path, FakeClock()).state["day"] is None


def test_unwritable_state_path_keeps_running_in_memory(tmp_path, pack, gitea):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    runner = make_runner(tmp_path, FakeClock(), state_path=str(blocker / "state.json"),
                         arc_provider=arc())
    producer = ListProducer()
    tick(runner, pack, producer)
    assert producer.types()[0] == "day_start"
    assert tick(runner, pack, producer) == []


# ── construction, config and the ceo_idle_tick hook ──────────────────────────
def test_build_day_runner_reads_config_and_plugins(tmp_path, pack, monkeypatch):
    mod = types.ModuleType("fake_office_plugins")
    mod.build_playlist = lambda cfg: FakePlaylist()
    mod.build_arc = lambda cfg: arc()
    monkeypatch.setitem(sys.modules, "fake_office_plugins", mod)
    cfg = ceo_cfg(pack, state_path=str(tmp_path / "s.json"), tz="America/New_York",
                  epoch="2026-09-27", stall_minutes=20, max_follow_ups=1,
                  replay_target="tuber_3", corpus_export=str(tmp_path / "e.jsonl"),
                  backlog_label="stream-task", playlist="fake_office_plugins:build_playlist",
                  arc_provider="fake_office_plugins:build_arc",
                  activity_paths=[str(tmp_path / "state.json")])
    runner = dr.build_day_runner(cfg, worker_id=CEO)
    assert isinstance(runner.playlist, FakePlaylist) and callable(runner.sources["arc"])
    assert isinstance(runner.sources["feature"], dr.CorpusFeatureSource)
    assert runner.sources["backlog"].label == "stream-task"
    assert runner.stall_s == 1200 and runner.max_follow_ups == 1
    assert runner.replay_target == "tuber_3" and runner.activity_probe is not None
    assert str(runner.epoch) == "2026-09-27"


def test_build_day_runner_bad_plugin_spec_is_absent(pack, capsys):
    runner = dr.build_day_runner(ceo_cfg(pack, state_path=None, playlist="nope",
                                         arc_provider="no_such_module_xyz:f", backlog=False))
    assert runner.playlist is None and runner.sources == {"arc": None, "feature": None,
                                                          "backlog": None}
    assert "plugin_unavailable" in capsys.readouterr().out


def test_load_factory_rejects_malformed_spec():
    assert dr.load_factory(None) is None
    with pytest.raises(ValueError):
        dr.load_factory("no_colon")


def test_day_runner_enabled_flags(pack):
    assert not dr.day_runner_enabled(ceo_cfg(pack))
    assert dr.day_runner_enabled(ceo_cfg(pack, stall_minutes=5))
    assert not dr.day_runner_enabled(ceo_cfg(pack, enabled=False))
    cfg = ceo_cfg(pack)
    cfg["office"]["day_runner"] = True
    assert dr.day_runner_enabled(cfg)


def test_ceo_idle_tick_autoinstalls_runner_from_config_once(tmp_path, pack, monkeypatch):
    built = []
    real = dr.build_day_runner

    def build(cfg, **kw):
        built.append(kw)
        return real(cfg, clock=FakeClock(DAY, 6, 0), **kw)
    monkeypatch.setattr(dr, "build_day_runner", build)
    monkeypatch.setattr(office, "build_gitea_client", lambda cfg: None)
    cfg = ceo_cfg(pack, state_path=str(tmp_path / "s.json"), backlog=False)
    producer = ListProducer()
    sent = office.ceo_idle_tick(CEO, cfg, ScriptedLLM("ceo"), producer)
    assert isinstance(office.get_day_runner(), dr.DayRunner)
    assert [m["type"] for m in sent][:2] == ["day_start", "phase_change"]
    office.ceo_idle_tick(CEO, cfg, ScriptedLLM("ceo"), producer)
    assert len(built) == 1 and built[0]["worker_id"] == CEO


def test_ceo_idle_tick_without_config_stays_noop(pack):
    assert office.ceo_idle_tick(CEO, ceo_cfg(pack), ScriptedLLM("ceo"), ListProducer()) is None
    assert office.get_day_runner() is None


def test_explicit_set_day_runner_wins_over_config(pack):
    office.set_day_runner(lambda *a: "mine")
    cfg = ceo_cfg(pack, state_path=None)
    assert office.ceo_idle_tick(CEO, cfg, None, ListProducer()) == "mine"


def test_tick_failure_is_swallowed(tmp_path, pack, capsys):
    def bad_clock():
        raise RuntimeError("clock broke")
    runner = dr.DayRunner(clock=bad_clock, state_path=None)
    assert runner(CEO, ceo_cfg(pack), None, ListProducer()) == []
    assert "tick_failed" in capsys.readouterr().out


def test_playlist_satisfies_protocol():
    assert isinstance(FakePlaylist(), dr.Playlist)


def test_unknown_time_zone_is_value_error():
    with pytest.raises(ValueError):
        dr.DayRunner(tz="Mars/Olympus", state_path=None)


# ── batch B: the wrap-up line on the live transcript ─────────────────────────
@pytest.mark.parametrize("live", [True, False])
def test_wrap_up_line_published_to_live_transcript_only_when_opted_in(tmp_path, pack, gitea, live):
    clock, producer = FakeClock(DAY, 6, 0), ListProducer()
    runner = make_runner(tmp_path, clock, arc_provider=arc())
    cfg = ceo_cfg(pack)
    if live:
        cfg["office"]["live_transcript"] = True
    runner(CEO, cfg, ScriptedLLM("ceo"), producer, None)
    producer.clear()
    clock.set(DAY, 23, 45)
    sent = runner(CEO, cfg, ScriptedLLM("ceo"), producer, None)
    # (phase_change(ship) first: the runner skipped 18:00.) office_line never joins `sent`.
    assert [m["type"] for m in sent] == ["phase_change", "wrap_up"]
    wrap = sent[-1]
    validate_message(wrap)
    lines = [m for m in producer.sent if m["type"] == "office_line"]
    if live:
        [line] = lines
        assert line["to"] == "roundtable" and line["payload"]["seat"] == CEO
        assert line["correlation_id"] == wrap["correlation_id"]
        assert line["payload"]["text"]
    else:
        assert lines == []


@pytest.mark.parametrize("say", [True, False])
def test_wrap_up_line_is_a_character_say_to_the_room(tmp_path, pack, gitea, say):
    """The CEO's 23:45 wrap-up line goes out through publish_office_line, so
    with agent.office.character_say it is also ONE v4 character_say: no
    addressees (it asks the whole team), scene office-<day>-ship."""
    clock, producer = FakeClock(DAY, 6, 0), ListProducer()
    runner = make_runner(tmp_path, clock, arc_provider=arc())
    cfg = ceo_cfg(pack)
    cfg["office"]["character_say"] = say
    runner(CEO, cfg, ScriptedLLM("ceo"), producer, None)
    producer.clear()
    clock.set(DAY, 23, 45)
    sent = runner(CEO, cfg, ScriptedLLM("ceo"), producer, None)
    wrap = sent[-1]
    says = [m for m in producer.sent if m["type"] == "character_say"]
    assert all(m not in sent for m in says)                 # never part of the day's script
    if not say:
        assert says == []
        return
    [line] = says
    assert line["from"] == CEO and line["correlation_id"] == wrap["correlation_id"]
    assert line["payload"]["scene_id"] == f"office-{DAY}-ship"
    assert line["payload"]["character"] == "ceo" and line["payload"]["addressees"] == []
    assert len(line["payload"]["present"]) == 8 and line["payload"]["text"].strip()
