"""Unattended week (golive plan D1/A1-A3): TableRuntime on the block clock.

Spine first inside a block's real window, then plot-free ambient scenes; carry-over; ambient
forever after the last block; restart-safe from the position file; a stuck scene never halts
the run. Unit tests only: fake LLM / producer / clocks, no containers.
"""
import datetime as dt
import json
import itertools

import pytest

from pending import require

gm = require("agent_handlers.table_gm", "app/agent_handlers/table_gm.py", wp="P3.6")
from message_bus import build_message  # noqa: E402
from table import ambient_source, control, protocol  # noqa: E402
from test_table_gm_longrun import Clock, Prod, starts  # noqa: E402

EDT = dt.timezone(dt.timedelta(hours=-4))
SEATS = ["tuber_1", "tuber_2"]
NAMES = {"gm": "GM", "tuber_0": "GM", "tuber_1": "Tess", "tuber_2": "Ana"}
SEAT_OF = {"tech_lead": "tuber_1", "analyst": "tuber_2", "ceo": "tuber_0", "party_member": "tuber_7"}
BLOCK = {"anchor": "2026-10-25T00:00:00-04:00", "block_hours": 6,
         "prelude_from": "2026-10-24T20:00:00-04:00", "offset_s": 0, "tz": "America/New_York"}
SPINE = {"seg0": ["s1", "s2"], "seg1": ["s3"], "seg2": ["s4"], "seg3": ["s5"]}


def ts(day, hh, mm=0, month=10):
    return dt.datetime(2026, month, day, hh, mm, tzinfo=EDT).timestamp()


class Wall:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Words:
    """Scripted GM with endless unique text (the repeat guards reject copies)."""

    def __init__(self):
        self.n = itertools.count()
        self.down = False
        self.systems = []           # (kind, system prompt)
        self.resolved = True
        self.poison = None          # substring of a system prompt that makes the GM call fail

    def complete_stream(self, system_prompt, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, reasoning_budget=None):
        if self.down or (self.poison and self.poison in system_prompt):
            raise RuntimeError("vllm down")
        kind = "adjudicate" if "Adjudicate the round" in system_prompt else "direct"
        self.systems.append((kind, system_prompt))
        if kind == "adjudicate":
            return "", json.dumps({"verdict": "pass", "retake_seat": None, "notes": "they chatted",
                                   "resolved": self.resolved, "state_delta": {"mood": "calm"}})
        n = next(self.n)
        return "", " ".join(f"gm{n}w{j}" for j in range(9))


def contract(sid, seg, n):
    return {"scene_id": sid, "run_id": "week", "segment_id": seg, "leaf_id": "leaf", "slot_id": sid,
            "order": n, "canon_goal": f"Spine goal {sid} alpha{n} beta{n}.", "participants": SEATS,
            "lore": [], "continuity_in": "", "continuity_out": "", "must_resolve": [],
            "expected_beats": [], "max_rounds": 1, "max_retries_per_turn": 1}


def amb_doc(sid, phase, seats=("tech_lead", "analyst"), **over):
    return {"id": sid, "title": sid, "ambient": True, "prompt": f"Ambient prompt {sid}.", "lore": [],
            "source": {}, "day_phase": phase, "seats": list(seats), **over}


def pool_of(*docs):
    return ambient_source.AmbientPool([ambient_source.parse_scene(d) for d in docs])


EVENING3 = [amb_doc(f"ev{i}", "evening") for i in range(3)]
NIGHT2 = [amb_doc(f"nt{i}", "off_hours", intensity="quiet") for i in range(2)]
DAY2 = [amb_doc(f"wd{i}", "workday") for i in range(2)]
MORNING2 = [amb_doc(f"mo{i}", "morning") for i in range(2)]
POOL = pool_of(*EVENING3, *NIGHT2, *DAY2, *MORNING2)


@pytest.fixture(autouse=True)
def providers(monkeypatch):
    gm.reset_runtimes()
    contracts = [contract(sid, seg, n) for n, (seg, sid) in enumerate(
        (seg, sid) for seg, ids in SPINE.items() for sid in ids)]
    monkeypatch.setattr(gm, "contracts_provider", lambda c: [dict(x) for x in contracts])
    monkeypatch.setattr(gm, "segments_provider", lambda c: {seg: {} for seg in SPINE})
    monkeypatch.setattr(gm, "context_provider",
                        lambda c, k, **kw: "CTX " + str(k.get("canon_goal", "")))
    holder = {"pool": POOL}
    monkeypatch.setattr(gm, "ambient_pool_provider", lambda c: holder["pool"])
    yield holder
    gm.reset_runtimes()


def cfg(tmp_path, block=None, **over):
    table = {"seats": SEATS, "gm_seat": "tuber_0", "seat_names": NAMES, "seat_of": SEAT_OF,
             "start_mode": "auto", "think_s": 60, "speak_s": 45, "auto_advance": True,
             "position_file": str(tmp_path / "pos.json"), "run_id": "week",
             "block_clock": dict(BLOCK, **(block or {})),
             "ambient": {"rounds": {"normal": 2, "quiet": 1}, "no_repeat_s": 48 * 3600}}
    table.update(over)
    return {"role": "table_gm", "table": table}


class Rig:
    """One GM 'process': config + fake LLM/producer/clocks. restart() = a new process."""

    def __init__(self, c, wall_t, llm=None):
        self.c, self.llm, self.prod = c, llm or Words(), Prod()
        self.clock, self.wall = Clock(), Wall(wall_t)
        self.lines = itertools.count()

    @property
    def rt(self):
        return gm.runtime_for("tuber_0")

    def restart(self):
        gm.reset_runtimes()
        self.prod = Prod()          # a fresh process has a fresh producer

    def tick(self):
        gm.table_gm_idle_tick("tuber_0", self.c, self.llm, self.prod, None,
                              clock=self.clock, wall=self.wall)

    def answer(self):
        seen = getattr(self.prod, "_seen", 0)
        while seen < len(self.prod.sent):
            m = self.prod.sent[seen]
            seen += 1
            p = m["payload"]
            if m["type"] == "think_request":
                for s in p["seats"]:
                    self.rt.on_message(protocol.build("think_done", s, m["from"], {
                        "scene_id": p["scene_id"], "round": p["round"], "seat": s, "ok": True}))
            elif m["type"] in ("turn_assignment", "retake"):
                self.rt.on_message(protocol.build("character_reply", p["seat"], m["from"], {
                    "scene_id": p["scene_id"], "round": p["round"], "seat": p["seat"], "took": True,
                    "reason": "", "text": f"Line {next(self.lines)} about topic{next(self.lines)}."}))
        self.prod._seen = seen

    def pump(self, steps=60, wall_step=0.0):
        for _ in range(steps):
            self.tick()
            if self.rt is not None:
                self.answer()
            self.wall.t += wall_step

    def pump_scenes(self, n, cap=400):
        """Play until n scenes have STARTED (and the last finished)."""
        for _ in range(cap):
            self.tick()
            self.wall.t += 1                    # real time passes between ticks
            if self.rt is not None:
                self.answer()
            if len(starts(self.prod)) >= n and not self.rt.running():
                self._flush()
                return
        raise AssertionError(f"only {len(starts(self.prod))} scenes started: {starts(self.prod)}")

    def _flush(self):
        """The scene resolved inside on_message; let tick() do its book-keeping WITHOUT
        starting the next scene (so the caller sees the state right after scene n)."""
        rt = self.rt
        if rt.arbiter is None:
            return
        rt.ensure_started = lambda: None
        try:
            self.tick()
        finally:
            del rt.ensure_started

    def started(self):
        return starts(self.prod)

    def status(self):
        return self.rt.status()


def kinds(ids):
    return ["ambient" if i.startswith("ambient.") else i for i in ids]


# ------------------------------------------------------------------ scheduling
def test_prelude_plays_ambient_evening(tmp_path):
    r = Rig(cfg(tmp_path), ts(24, 21, 0))
    r.pump_scenes(2)
    assert all(i.startswith("ambient.") for i in r.started())
    sent = [protocol.parse(m)["contract"] for m in r.prod.sent if m["type"] == "scene_start"]
    assert {c["day_phase"] for c in sent} == {"evening"} and all(c["ambient"] for c in sent)
    b = r.status()["block"]
    assert (b["window"], b["phase"], b["index"]) == ("prelude", "evening", -1)
    assert r.rt.last_index is None                  # the spine position never moved


def test_block_window_plays_the_spine_first_then_ambient_until_the_window_ends(tmp_path):
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.pump_scenes(5)
    assert kinds(r.started()) == ["s1", "s2", "ambient", "ambient", "ambient"]
    assert r.status()["block"]["spine_remaining"] == 0 and r.status()["block"]["phase"] == "off_hours"
    # still in block 0's window: nothing but ambient
    r.pump_scenes(7)
    assert kinds(r.started())[2:] == ["ambient"] * 5
    # block 1 opens: its spine comes next
    r.wall.t = ts(25, 6, 1)
    r.pump_scenes(8)
    assert kinds(r.started())[7:] == ["s3"]


def test_unplayed_spine_carries_over_and_plays_before_the_next_blocks_spine(tmp_path):
    pos = tmp_path / "pos.json"
    pos.write_text(json.dumps({"index": 0, "scene_id": "s1", "played": ["s1"], "story": []}))
    r = Rig(cfg(tmp_path), ts(25, 6, 30))              # block 1; s2 of block 0 never played
    r.pump_scenes(3)
    assert kinds(r.started()) == ["s2", "s3", "ambient"]
    assert r.status()["block"]["carry_over"] == 0


def test_status_reports_carry_over_before_it_is_played(tmp_path):
    r = Rig(cfg(tmp_path, start_mode="manual"), ts(25, 13, 0))      # block 2, nothing played
    r.tick()
    b = r.status()["block"]
    assert (b["index"], b["carry_over"], b["spine_remaining"]) == (2, 3, 1)
    assert b["next_kind"] == "spine" and b["next_spine_scene"] == "s1"
    assert r.status()["next_scene_id"] == "s1"


def test_past_the_last_block_it_plays_ambient_forever_and_never_loops_the_spine(tmp_path):
    r = Rig(cfg(tmp_path), ts(26, 9, 0))                # Monday 09:00, after block 3 (ends Mon 00:00)
    r.pump_scenes(12)
    assert all(i.startswith("ambient.") for i in r.started())
    st = r.status()
    assert st["block"]["window"] == "after" and st["run"]["finished"] is False
    assert st["run"]["active"] is True and r.rt.mode == "auto"
    r.wall.t = ts(3, 3, 0, month=11)                    # a week later still going, night phase
    r.pump_scenes(14)
    assert all(i.startswith("ambient.") for i in r.started())
    assert r.status()["block"]["phase"] == "off_hours"


def test_run_can_be_given_an_end_after_the_last_block(tmp_path):
    r = Rig(cfg(tmp_path, block={"end_after_last_block_s": 3600}), ts(26, 0, 30))
    r.pump_scenes(2)
    n = len(r.started())
    r.wall.t = ts(26, 1, 5)
    r.pump(40)
    assert len(r.started()) == n and r.status()["run"]["finished"] is True


def test_offset_s_pretends_it_is_block_n_now(tmp_path):
    friday = ts(23, 10, 0)
    shift = ts(25, 7, 30) - friday                      # Friday 10:00 behaves as Sunday 07:30 (block 1)
    r = Rig(cfg(tmp_path, block={"offset_s": shift}), friday)
    r.pump_scenes(4)
    assert kinds(r.started()) == ["s1", "s2", "s3", "ambient"]      # carry-over of block 0 first
    b = r.status()["block"]
    assert (b["index"], b["phase"], b["offset_s"]) == (1, "morning", shift)
    assert b["effective_now"].startswith("2026-10-25T07:3")


# ------------------------------------------------------------------ restart safety
def test_restart_mid_block_resumes_from_the_persisted_state(tmp_path):
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.pump_scenes(3)                                    # s1, s2, one ambient
    assert kinds(r.started()) == ["s1", "s2", "ambient"]
    saved = json.loads((tmp_path / "pos.json").read_text())
    assert saved["played"] == ["s1", "s2"] and len(saved["ambient_history"]) == 1
    first_ambient = saved["ambient_history"][0]["id"]
    r.restart()
    r.wall.t += 90                                      # the GM was down for a minute and a half
    r.pump_scenes(2)                                    # auto resumes by itself: no table_run_start
    assert r.rt.mode == "auto"
    ids = list(r.started())
    assert ids and all(i.startswith("ambient.") for i in ids)
    assert not any(i in ("s1", "s2") for i in ids)      # nothing replayed
    assert not ids[0].startswith(f"ambient.{first_ambient}.")      # ambient history survived
    assert [e.scene_id for e in r.rt.story.entries if not e.ambient] == ["s1", "s2"]


def test_restart_in_the_next_block_picks_up_the_new_spine_and_the_leftovers(tmp_path):
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.pump_scenes(1)                                    # s1 played, then the GM dies
    r.pump(0)
    r.restart()
    r.wall.t = ts(25, 6, 10)                            # comes back in block 1
    r.pump_scenes(3)
    assert kinds(r.started()) == ["s2", "s3", "ambient"]


def test_a_scene_cut_off_by_a_restart_is_replayed_once_then_given_up(tmp_path):
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.tick()                                            # s1 starts ...
    assert r.started() == ["s1"]
    r.restart()                                         # ... and the process dies mid-scene
    r.tick()
    assert r.started() == ["s1"]                        # attempt 2: replayed
    r.restart()                                         # dies again
    r.tick()
    assert r.started() == ["s2"]                        # crash loops are capped: s1 is given up
    saved = json.loads((tmp_path / "pos.json").read_text())
    assert saved["attempts"]["s1"] == 2 and "s1" not in saved["played"]


def test_a_position_file_from_before_the_block_clock_still_loads(tmp_path):
    story = [{"scene_id": "s1", "goal": "g", "notes": "n", "resolved": True},
             {"scene_id": "amb", "goal": "tea", "ambient": True}]
    (tmp_path / "pos.json").write_text(json.dumps({"index": 0, "scene_id": "s1", "story": story}))
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.pump_scenes(1)
    assert r.started() == ["s2"]                        # s1 known from the story log


def test_operator_run_from_beginning_resets_the_played_set(tmp_path):
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.pump_scenes(3)
    assert {"s1", "s2"} <= r.rt._played
    r.rt.on_message(build_message("operator", "tuber_0", control.RUN_START, {"from": "beginning"}))
    assert r.rt._played == set() and len(r.rt.story) == 0
    assert r.started()[-1] == "s1"                      # the week's spine starts over
    r.pump_scenes(len(r.started()))
    assert r.rt._played == {"s1"}


# ------------------------------------------------------------------ never halt on stuck
def test_stuck_scenes_never_halt_the_run(tmp_path):
    r = Rig(cfg(tmp_path, auto_max_consecutive_stuck=2, auto_stuck_cooldown_s=100,
                auto_stuck_cooldown_max_s=400), ts(25, 0, 5))
    r.llm.down = True
    for _ in range(30):
        r.tick()
        r.clock.t += 1
    rt = r.rt
    assert rt._halted is False                          # the old behaviour: halted after N
    assert rt._consecutive_stuck >= 2 and rt._stuck_total >= 2
    n = len(r.started())
    for _ in range(30):                                 # inside the cooldown nothing new starts
        r.tick()
        r.clock.t += 1
    assert len(r.started()) == n
    st = r.status()
    assert st["block"]["stuck"]["cooldown_s"] > 0 and st["block"]["stuck"]["total"] == rt._stuck_total
    assert st["run"]["halted"] is False and st["run"]["active"] is True
    # the model comes back: after the cooldown the table plays an ambient scene first, then goes on
    r.llm.down = False
    r.clock.t += 500
    r.pump_scenes(len(r.started()) + 3)
    after = r.started()[n:]
    assert after[0].startswith("ambient.")
    assert rt._consecutive_stuck == 0
    assert "s1" in after                                # the skipped spine scene is retried
    assert st["block"]["stuck"]["limit"] == 2


def test_cooldown_doubles_while_the_model_stays_down(tmp_path):
    r = Rig(cfg(tmp_path, auto_max_consecutive_stuck=1, auto_stuck_cooldown_s=100,
                auto_stuck_cooldown_max_s=300), ts(25, 0, 5))
    r.llm.down = True
    waits = []
    for _ in range(4):
        for _ in range(10):                             # get the scene stuck and skipped
            r.tick()
        waits.append(round(r.rt._cooldown_until - r.clock.t))
        r.clock.t = r.rt._cooldown_until + 1
    assert waits == [100, 200, 300, 300] and r.rt._halted is False


def test_a_stuck_spine_scene_is_retried_at_most_spine_max_attempts_times(tmp_path):
    r = Rig(cfg(tmp_path, auto_max_consecutive_stuck=9), ts(25, 0, 5))
    r.llm.poison = "alpha0"                             # s1's goal always breaks the GM
    r.pump_scenes(8)
    ids = [i for i in r.started() if not i.startswith("ambient.")]
    assert ids.count("s1") == 2 and "s2" in ids         # tried twice, then dropped for good
    assert "s1" not in r.rt._played
    assert r.rt._stuck_total == 2


# ------------------------------------------------------------------ ambient behaviour
def test_ambient_never_repeats_inside_the_window_when_the_pool_allows(tmp_path):
    r = Rig(cfg(tmp_path), ts(24, 21, 0))
    r.pump_scenes(3)
    ids = [i.split(".")[1] for i in r.started()]
    assert sorted(ids) == ["ev0", "ev1", "ev2"]
    r.pump_scenes(4)                                    # pool spent: reuses the least recently used
    assert r.started()[3].split(".")[1] == ids[0]
    assert r.status()["block"]["last_ambient_id"] == ids[0]


def test_ambient_scenes_are_plot_free_and_leave_the_spine_state_alone(tmp_path):
    r = Rig(cfg(tmp_path), ts(24, 21, 0))
    r.pump_scenes(2)
    rt = r.rt
    assert rt._played == set() and rt.last_index is None
    assert [e.ambient for e in rt.story.entries] == [True, True]
    for e in rt.story.entries:
        assert e.state_delta == {} and len(e.goal) <= 100 and len(e.notes) <= 120
    direct = [s for k, s in r.llm.systems if k == "direct"]
    assert direct and all("AMBIENT" in s and "plot-free" in s for s in direct)
    resolves = [protocol.parse(m) for m in r.prod.sent if m["type"] == "scene_resolve"]
    assert resolves and all(m["state_delta"] == {} for m in resolves)     # the GM said mood=calm


def test_spine_scenes_do_not_get_the_ambient_instruction(tmp_path):
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.pump_scenes(1)
    assert all("AMBIENT" not in s for k, s in r.llm.systems if k == "direct")
    resolves = [protocol.parse(m) for m in r.prod.sent if m["type"] == "scene_resolve"]
    assert resolves[0]["state_delta"] == {"mood": "calm"}


def test_ambient_keeps_a_night_of_filler_out_of_the_spine_memory(tmp_path):
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.pump_scenes(2 + 10)
    spine = [e.scene_id for e in r.rt.story.entries if not e.ambient]
    ambient = [e for e in r.rt.story.entries if e.ambient]
    assert spine == ["s1", "s2"] and len(ambient) <= 4
    text = r.rt._gm_history()
    assert "[s1]" in text and "[s2]" in text and text.count("ambient office life") == 1


def test_off_hours_ambient_is_one_quiet_round_and_evening_is_two(tmp_path):
    played = {"index": 3, "scene_id": "s2", "played": ["s1", "s2", "s3", "s4", "s5"], "story": []}
    (tmp_path / "pos.json").write_text(json.dumps(played))
    for when, rounds in ((ts(26, 3, 0), 1), (ts(26, 20, 0), 2)):
        r = Rig(cfg(tmp_path), when)
        r.llm.resolved = False                          # the GM never says "resolved": play max_rounds
        r.pump_scenes(1)
        directions = [m for m in r.prod.sent if m["type"] == "scene_direction"]
        assert len(directions) == rounds, when
        sc = [protocol.parse(m)["contract"] for m in r.prod.sent if m["type"] == "scene_start"][0]
        assert sc["max_rounds"] == rounds
        r.restart()
        (tmp_path / "pos.json").write_text(json.dumps(played))


def test_idle_gap_between_scenes_follows_the_phase(tmp_path):
    played = {"index": 3, "scene_id": "s2", "played": ["s1", "s2", "s3", "s4", "s5"], "story": []}
    (tmp_path / "pos.json").write_text(json.dumps(played))
    ambient = {"rounds": {"normal": 1, "quiet": 1}, "no_repeat_s": 48 * 3600,
               "gap_s": {"off_hours": [60, 60], "evening": [5, 5]}}
    night = Rig(cfg(tmp_path, ambient=ambient), ts(26, 3, 0))
    night.pump_scenes(1)
    night.tick()
    n = len(night.started())
    for _ in range(10):                                 # 10 s later: still inside the 60 s gap
        night.clock.t += 1
        night.tick()
    assert len(night.started()) == n
    night.clock.t += 60
    night.pump_scenes(n + 1)
    assert len(night.started()) == n + 1


def test_empty_ambient_pool_idles_without_crashing_and_recovers(tmp_path, providers):
    providers["pool"] = ambient_source.AmbientPool()
    r = Rig(cfg(tmp_path), ts(24, 21, 0))
    r.pump(20)
    assert r.started() == [] and r.status()["block"]["ambient_pool"] == 0
    providers["pool"] = POOL
    r.wall.t += 120                                     # the pool reloads within a minute
    r.pump_scenes(1)
    assert r.started()[0].startswith("ambient.")


def test_ambient_pool_provider_failure_keeps_the_table_alive(tmp_path, monkeypatch):
    def boom(c):
        raise RuntimeError("generator db down")
    monkeypatch.setattr(gm, "ambient_pool_provider", boom)
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.pump_scenes(2)
    assert kinds(r.started()) == ["s1", "s2"]           # the spine does not depend on the filler
    r.pump(10)
    assert r.rt.running() is False and r.status()["block"]["ambient_pool"] == 0


def test_generator_db_down_at_boot_plays_ambient_then_picks_the_spine_up(tmp_path, monkeypatch):
    real = gm.contracts_provider
    state = {"down": True}

    def flaky(c):
        if state["down"]:
            raise RuntimeError("generator db down")
        return real(c)
    monkeypatch.setattr(gm, "contracts_provider", flaky)
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.pump_scenes(2)
    assert all(i.startswith("ambient.") for i in r.started())
    state["down"] = False
    r.wall.t += 60
    r.pump_scenes(len(r.started()) + 1)
    assert r.started()[-1] == "s1" and r.status()["block"]["index"] == 0


# ------------------------------------------------------------------ status + manual controls
def test_status_exposes_the_block_view(tmp_path):
    r = Rig(cfg(tmp_path), ts(25, 0, 5))
    r.pump_scenes(3)
    b = r.status()["block"]
    for key in ("index", "phase", "window", "spine_remaining", "carry_over", "ambient_pool",
                "last_ambient_id", "stuck", "block_start", "block_end", "next_kind"):
        assert key in b, key
    assert b["ambient_pool"] == len(POOL) and b["last_ambient_id"]
    assert b["stuck"] == {"consecutive": 0, "total": 0, "limit": 3, "cooldown_s": 0.0}
    assert b["block_start"].startswith("2026-10-25T00:00") and b["block_end"].startswith("2026-10-25T06:00")
    json.dumps(r.status())                              # the payload must stay JSON-safe


def test_manual_mode_and_operator_controls_still_work(tmp_path):
    r = Rig(cfg(tmp_path, start_mode="manual"), ts(25, 0, 5))
    r.pump(10)
    assert r.started() == []                            # waits for the operator
    r.rt.on_message(build_message("operator", "tuber_0", control.SCENE_REQUEST, {"scene_id": "s3"}))
    assert r.started() == ["s3"]
    r.pump_scenes(1)
    assert "s3" in r.rt._played                         # a manually played scene counts as played
    r.rt.on_message(build_message("operator", "tuber_0", control.RUN_START, {"from": "next"}))
    r.pump_scenes(2)
    assert r.rt.mode == "auto"
    r.rt.on_message(build_message("operator", "tuber_0", control.RUN_STOP, {"now": True}))
    n = len(r.started())
    r.pump(20)
    assert r.rt.mode == "manual" and len(r.started()) == n


def test_without_block_clock_the_table_behaves_as_before(tmp_path):
    c = cfg(tmp_path)
    del c["table"]["block_clock"]
    r = Rig(c, ts(25, 0, 5))
    r.pump_scenes(5)
    assert r.started() == ["s1", "s2", "s3", "s4", "s5"]
    r.pump(10)
    assert "block" not in r.status()
    assert "played" not in json.loads((tmp_path / "pos.json").read_text())


def test_a_scene_that_never_finishes_is_cut_by_the_watchdog_not_waited_on(tmp_path):
    r = Rig(cfg(tmp_path, scene_max_s=600), ts(25, 0, 5))
    r.tick()                                            # s1 starts; the seats never answer
    assert r.started() == ["s1"]
    r.clock.t += 601
    r.tick()
    assert r.rt._stuck_total == 1 and r.rt._halted is False
    assert r.started() == ["s1", "s2"]                  # carried on at once with the next scene
    assert "s1" not in r.rt._played                     # not played: retried after a good scene
    r.pump_scenes(len(r.started()))
    assert r.rt._consecutive_stuck == 0


def test_block_mode_skips_stuck_scenes_even_when_auto_skip_stuck_is_off(tmp_path):
    r = Rig(cfg(tmp_path, auto_skip_stuck=False, auto_max_consecutive_stuck=50), ts(25, 0, 5))
    r.llm.down = True
    for _ in range(12):
        r.tick()
    assert r.rt._stuck_total >= 2 and r.rt._halted is False


# ------------------------------------------------------------------ office config + generator
def _office_build(tmp_path, **over):
    import importlib.util
    import pathlib
    import yaml
    repo = pathlib.Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("build_table_configs", repo / "scripts" / "build_table_configs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    doc = yaml.safe_load((repo / "config" / "table" / "ashiorid_office.yaml").read_text())
    doc.update(over)
    src = tmp_path / "ashiorid_office.yaml"
    src.write_text(yaml.safe_dump(doc))
    out = tmp_path / "out"
    mod.build(src, out)
    return yaml.safe_load((out / "ceo.yaml").read_text())["agent"]["table"], out


def test_office_config_is_set_up_for_the_unattended_week(tmp_path):
    t, out = _office_build(tmp_path)
    assert t["start_mode"] == "auto" and t.get("auto_duration_s") is None
    assert t["auto_pace"] == {"words_per_s": 2.9, "per_line_s": 2.2, "lead_s": 25}
    assert t["adjudicate"]["max_tokens"] == 512
    assert t["block_clock"]["anchor"] == "2026-10-25T00:00:00-04:00" and t["block_clock"]["block_hours"] == 6
    assert t["block_clock"]["prelude_from"] == "2026-10-24T20:00:00-04:00"
    assert t["ambient"]["gap_s"]["off_hours"] == [60, 180] and t["ambient"]["no_repeat_s"] == 172800
    assert t["run_id"] == "ashiorid_office_20260929_135927_9c1b"        # the old run until told
    import yaml
    assert yaml.safe_load((out / "roundtable.yaml").read_text())["agent"]["live"]["max_age_s"] == 1800
    assert gm.block_clock.BlockClock.from_config(t["block_clock"]) is not None
    assert "GENERATOR_POSTGRES_HOST" in (out / "docker-compose.table-office.yml").read_text()


def test_week_run_id_swaps_the_story_and_gives_it_a_fresh_position_file(tmp_path):
    t, _ = _office_build(tmp_path, week_run_id="ashiorid_office_week_20261022_abcd")
    assert t["run_id"] == "ashiorid_office_week_20261022_abcd"
    assert t["contracts_fixture"] == "/data/table/fixtures/ashiorid_office_week_20261022_abcd.json"
    assert "skip_scenes" not in t
    assert "ashiorid_office_week_20261022_abcd" in t["position_file"]
    assert "week_run_id" not in t


def test_generated_ambient_settings_drive_the_runtime(tmp_path):
    t, _ = _office_build(tmp_path)
    c = {"role": "table_gm", "table": dict(t, position_file=str(tmp_path / "pos.json"),
                                           contracts_fixture=None)}
    rt = gm.TableRuntime("tuber_0", c, Words(), Prod(), wall=Wall(ts(24, 21)))
    assert rt._block is not None and rt._ambient_cfg["rounds"] == {"normal": 2, "quiet": 1}
    assert rt._gap_for(type("D", (), {"kind": "ambient", "phase": "off_hours"})()) >= 60
    assert 10 <= rt._gap_for(type("D", (), {"kind": "ambient", "phase": "workday"})()) <= 30
