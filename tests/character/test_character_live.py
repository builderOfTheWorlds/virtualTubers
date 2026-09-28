"""WP-24 tests for app/character/live.py: the live driver and ObservingRenderer.

Frozen test list (playbook §4 WP-24, items 1-7 and 9; item 8 is in
test_character_jobs_story.py). Plan §8 (live driver), D-18, D-19: the driver
builds CampaignRuntime, keeps one LLMImproviser per cast member whose
system_prompt is the brief (never `carry`/`loop`, which would print "Loop N"),
and uses ObservingRenderer, a SceneRenderer subclass that calls the parent,
then publishes character_say (dialogue) or scene_event (narration/action),
feeds recall and observe()s every improviser. It stops between scenes when
`char-live:<campaign>` is disabled or the week ends, then publishes story_end.

OB-41 test corrections (tracker .claude/prompts/character_v4_tracker_phase3b.md):
- "the real campaigns/hptest pack" -> the real campaigns/ashiorid_office pack
  (OB-41 re-pilots v4 on the office cast). Its one spine scene is scripted, so
  items 3 and 7 flip its dialogue beats to improv in memory (the pack files
  are not changed).
- The office GM is the CEO, who is also a character; the Party Member never
  speaks: his brief still holds ("You never speak. You only observe."), his LLM
  is never called, and nothing is ever published as him (no character_say for
  him, nothing from char:party_member).
Fakes: tests/character/fakes_e2e.py (producer, WorkerControl, LLM, judge).
"""
import copy
import dataclasses
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone

import pytest


from fakes import seed_character
from fakes_e2e import (FIXTURES, OFFICE_PACK, REPO_ROOT, FakeProducer, FakeWorkerControl,
                       ScriptedLLM, count)
from pending import require, skip_if_pending

skip_if_pending("app/character/brief.py", wp="WP-22")
skip_if_pending("app/character/recall.py", wp="WP-23")
live = require("character.live", "app/character/live.py", wp="WP-24")
from campaign.pack import load_pack  # noqa: E402
from character import brief, recall  # noqa: E402
from character.clock import LoopClock  # noqa: E402
from replay import Pacer  # noqa: E402

CAMPAIGN = "ashiorid_office"
DRIVER = "char-live:ashiorid_office"
WED_W2 = datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc)


class FakeRecall:
    """A RecallEngine stand-in: observe() returns scripted events, once each."""

    def __init__(self, script=None):
        self.script = dict(script or {})
        self.seen = []

    def observe(self, text, *, beat_id=None):
        self.seen.append(text)
        return self.script.pop(len(self.seen), [])


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _pack(improv=False):
    pack = load_pack(OFFICE_PACK)
    if improv:
        scene = pack.scenes["first-standup"]
        scene.beats = [dataclasses.replace(b, improv=True) if b.kind == "dialogue" else b
                       for b in scene.beats]
    return pack


def _driver(pack, *, producer=None, control=None, now=None, recall_for=None, loads=None,
            llms=None, record=None, max_scenes=1, max_recent=8):
    llms = {} if llms is None else llms
    loads = [] if loads is None else loads

    def loader(slug):
        loads.append(slug)
        return brief.parts_from_pack(slug, OFFICE_PACK)

    def llm_for(slug):
        return llms.setdefault(slug, ScriptedLLM(slug))

    return live.LiveDriver(
        pack, campaign=CAMPAIGN, clock=LoopClock(datetime(2026, 9, 27).date(), "America/New_York"),
        publish=(producer or FakeProducer()).send, control=control or FakeWorkerControl(),
        briefs=brief.BriefCache(loader, ttl_s=300), llm_for=llm_for, recall_for=recall_for,
        now=now or Clock(WED_W2), max_scenes=max_scenes, record=record, max_recent=max_recent,
        renderer_kwargs={"out": io.StringIO(), "pacer": Pacer(enabled=False)})


# T24.1
def test_observing_renderer_calls_parent_then_publishes_say_and_scene_events():
    pack = _pack()
    out = io.StringIO()
    sent = []
    renderer = live.ObservingRenderer(pack, campaign=CAMPAIGN,
                                      publish=lambda m: sent.append((copy.deepcopy(m), out.getvalue())),
                                      present=live.present_for(pack), out=out,
                                      pacer=Pacer(enabled=False))
    results = renderer.render_scene(pack.scenes["first-standup"])
    messages = [m for m, _ in sent]
    assert messages[0]["type"] == "scene_event" and messages[0]["payload"]["kind"] == "scene_start"
    assert messages[-1]["payload"]["kind"] == "scene_end"
    beats = messages[1:-1]
    assert len(beats) == len([r for r in results if r.text])
    present = live.present_for(pack)
    assert present == pack.player_ids + [pack.gm_id]
    for (message, out_so_far), rendered in zip(sent[1:-1], [r for r in results if r.text]):
        payload = message["payload"]
        assert payload["campaign"] == CAMPAIGN and payload["scene_id"] == "first-standup"
        assert payload["present"] == present and payload["text"] == rendered.text
        assert rendered.text in out_so_far                    # the parent rendered it first
        if rendered.kind == "dialogue":
            assert message["type"] == "character_say"
            assert message["from"] == f"char:{rendered.speaker}"
            assert payload["character"] == rendered.speaker
        else:
            assert message["type"] == "scene_event" and message["from"] == DRIVER
            assert payload["kind"] == rendered.kind
            assert payload["character"] == (None if rendered.kind == "narration" else rendered.speaker)
    kinds = [m["type"] for m in beats]
    assert kinds.count("character_say") == sum(1 for b in pack.scenes["first-standup"].beats
                                               if b.kind == "dialogue")
    assert not any(m["type"] == "character_say" and m["payload"]["character"] == "party_member"
                   for m in messages)


# T24.2
def test_every_improviser_observes_every_beat():
    pack = _pack()
    driver = _driver(pack, max_recent=100)
    assert driver.run() == 0
    assert set(driver.improvisers.improvisers) == set(pack.cast)
    transcripts = [imp.recent for imp in driver.improvisers.improvisers.values()]
    assert all(t == transcripts[0] for t in transcripts)
    assert len(transcripts[0]) == 21                       # enter narration + 20 text-bearing beats
    assert transcripts[0][0].startswith(f"{live.NARRATION_NAME}: Tallow Street, third floor.")
    assert any(line.startswith("Graham Ellery: Morning, all.") for line in transcripts[0])


# T24.3
def test_each_cast_members_system_prompt_is_the_brief_and_never_says_loop():
    pack = _pack(improv=True)
    extra = copy.deepcopy(pack.scenes["first-standup"].beats[0])
    pack.scenes["first-standup"].beats.append(dataclasses.replace(
        extra, kind="dialogue", speaker="party_member", text="(silence)", improv=True))
    llms, producer = {}, FakeProducer()
    driver = _driver(pack, llms=llms, producer=producer)
    assert driver.run() == 0
    speakers = {b.speaker for b in pack.scenes["first-standup"].beats
                if b.kind == "dialogue" and b.speaker != "party_member"}
    assert speakers <= set(llms) and speakers
    for slug in speakers:
        member = driver.improvisers.member_for(slug)
        assert member.system_prompt == driver.brief_for(slug)
        for call in llms[slug].calls:
            assert driver.brief_for(slug) in call["system"]
            assert "Loop" not in call["system"]
            assert all("Loop" not in m["content"] for m in call["messages"])
    silent = driver.improvisers.member_for("party_member")
    assert "You never speak. You only observe." in silent.system_prompt
    assert "Loop" not in silent.system_prompt
    assert "party_member" not in llms or llms["party_member"].calls == []
    assert not any(m["from"] == "char:party_member" for m in producer.sent)
    assert not any(m["type"] == "character_say" and m["payload"]["character"] == "party_member"
                   for m in producer.sent)
    assert live.default_silent(pack) == frozenset({"party_member"})


# T24.4
def test_character_refresh_invalidates_cache_clears_transcripts_and_resets_recall():
    pack = _pack()
    loads, made = [], []

    def recall_for(slug):
        made.append(slug)
        return FakeRecall()

    driver = _driver(pack, loads=loads, recall_for=recall_for)
    first_engines = dict(driver.engines)
    driver.brief_for("engineer")
    driver.brief_for("engineer")
    assert loads == ["engineer"]
    driver.improvisers.observe("Theo Palliser", "Okay okay so.")
    driver.surfaced["engineer"].append("a feeling surfaces: something")
    driver.handle_message({"type": "character_refresh", "from": "character-updater",
                           "payload": {"campaign": "some_other_show", "week": 2,
                                       "characters": ["*"], "reason": "weekly_reset"}})
    assert driver.surfaced["engineer"]                      # another campaign: ignored
    driver.handle_message({"type": "character_say", "payload": {"campaign": CAMPAIGN}})
    driver.handle_message({"type": "character_refresh", "from": "character-updater",
                           "payload": {"campaign": CAMPAIGN, "week": 2, "characters": ["*"],
                                       "reason": "weekly_reset"}})
    assert all(imp.recent == [] for imp in driver.improvisers.improvisers.values())
    assert driver.surfaced["engineer"] == []
    assert sorted(made) == sorted(list(first_engines) * 2)
    assert all(driver.engines[slug] is not first_engines[slug] for slug in first_engines)
    driver.brief_for("engineer")
    assert loads == ["engineer", "engineer"]


# T24.5
def test_disable_finishes_the_scene_then_publishes_story_end_and_exits_0():
    pack = _pack()
    control = FakeWorkerControl()
    producer = FakeProducer(on_send=lambda m: len(producer.sent) == 5 and control.set_enabled(DRIVER, False))
    driver = _driver(pack, producer=producer, control=control, max_scenes=5)
    assert driver.run() == 0
    assert DRIVER in control.checks
    types = [(m["type"], m["payload"].get("kind")) for m in producer.sent]
    assert types.count(("scene_event", "scene_start")) == 1
    assert types.count(("scene_event", "scene_end")) == 1
    assert types[-1] == ("scene_event", "story_end") and types[-2] == ("scene_event", "scene_end")
    assert driver.runtime.state.history == ["first-standup"]


# T24.6
def test_stops_the_same_way_at_the_weeks_ends_at():
    pack = _pack()
    clock = Clock(datetime(2026, 10, 11, 3, 59, tzinfo=timezone.utc))   # Sat 23:59 NY, week 2

    def tick(message):
        clock.now += timedelta(seconds=30)

    producer = FakeProducer(on_send=tick)
    driver = _driver(pack, producer=producer, now=clock, max_scenes=5)
    assert driver.run() == 0
    kinds = [m["payload"].get("kind") for m in producer.sent if m["type"] == "scene_event"]
    assert kinds.count("scene_start") == 1 and kinds[-2:] == ["scene_end", "story_end"]
    story_end = producer.sent[-1]
    assert story_end["from"] == DRIVER and story_end["payload"]["campaign"] == CAMPAIGN


# T24.7
def test_recall_surface_injects_the_feeling_into_the_next_brief_for_that_character_only():
    pack = _pack(improv=True)
    gist = "My stomach dropped when the latency graph went red."
    surface = recall.RecallEvent(level="surface", fragment_id="f1", gist=gist, activation=0.7,
                                 signals={"hooks": 1.0, "trajectory": 0.6, "gist": 0.1},
                                 beat_index=1, judge_verdict=True, judge_reason="yes")
    unease = dataclasses.replace(surface, level="unease", fragment_id="f2", judge_verdict=None)
    engines = {"engineer": FakeRecall({1: [surface]}), "tester": FakeRecall({1: [unease]})}
    recorded, llms = [], {}
    driver = _driver(pack, llms=llms, recall_for=lambda slug: engines.get(slug),
                     record=lambda slug, event: recorded.append((slug, event.level)))
    assert driver.run() == 0
    line = recall.surface_text(gist)
    assert all(line in call["system"] for call in llms["engineer"].calls)
    assert line in driver.brief_for("engineer")
    for slug, llm in llms.items():
        if slug != "engineer":
            assert all(line not in call["system"] for call in llm.calls)
    tester_calls = [call["system"] for call in llms["tester"].calls]
    assert recall.UNEASE_TEXT in tester_calls[0]            # once, on the next brief only
    assert all(recall.UNEASE_TEXT not in system for system in tester_calls[1:])
    assert sorted(recorded) == [("engineer", "surface"), ("tester", "unease")]
    assert len(engines["engineer"].seen) == 21              # recall saw every text-bearing beat


# T24.9
def test_no_file_under_app_campaign_is_modified():
    manifest = json.loads((FIXTURES / "app_campaign_sha256.json").read_text(encoding="utf-8"))["files"]
    root = REPO_ROOT / "app" / "campaign"
    actual = {p.relative_to(REPO_ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n"))
              .hexdigest() for p in sorted(root.rglob("*"))
              if p.is_file() and "__pycache__" not in p.parts}
    assert actual == manifest


# plan §7: every unease or surface event writes a fragment_recalls row; a judged
# surface also inserts the permanent fragment_unlocks row (D-08). The driver's
# `record` callback and the WP-25 e2e test use this.
@pytest.mark.integration
def test_record_recall_event_writes_the_recall_and_unlocks_on_surface(pg, pg_conn):
    cid = seed_character(pg_conn, "engineer")
    with pg_conn.cursor() as cur:
        for fid in ("f1", "f2"):
            cur.execute("INSERT INTO memory_fragments (id, character_id, source_week, gist) "
                        "VALUES (%s, %s, 1, 'I felt the floor tilt.')", (fid, cid))
    pg_conn.commit()
    ts = datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc)
    base = dict(gist="I felt the floor tilt.", activation=0.6,
                signals={"hooks": 0.5, "trajectory": 0.8, "gist": 0.1}, beat_index=9,
                beat_id="first-standup#9")
    unease = recall.RecallEvent(level="unease", fragment_id="f1", **base)
    surface = recall.RecallEvent(level="surface", fragment_id="f2", judge_verdict=True,
                                 judge_reason="the same arc", **base)
    assert live.record_recall_event(pg_conn, character_id=cid, week=2, event=unease, ts=ts)
    recall_id = live.record_recall_event(pg_conn, character_id=cid, week=2, event=surface, ts=ts)
    pg_conn.commit()
    assert count(pg, "SELECT count(*) FROM fragment_recalls") == 2
    assert count(pg, "SELECT level || ':' || loop_week || ':' || beat_event_id FROM fragment_recalls "
                     "WHERE fragment_id = 'f1'") == "unease:2:first-standup#9"
    assert count(pg, "SELECT judge_verdict FROM fragment_recalls WHERE fragment_id = 'f2'") is True
    assert count(pg, "SELECT signals->>'trajectory' FROM fragment_recalls WHERE fragment_id = 'f2'") == "0.8"
    assert count(pg, "SELECT fragment_id || ':' || unlocked_week || ':' || recall_id "
                     "FROM fragment_unlocks") == f"f2:2:{recall_id}"
    assert count(pg, "SELECT count(*) FROM fragment_unlocks WHERE fragment_id = 'f1'") == 0
