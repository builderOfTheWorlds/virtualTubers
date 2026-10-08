"""P1.1 table_round probe: shape, ordering and privacy invariants (fake host)."""
import pathlib
import sys
import threading
import time

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "utilities" / "benchmarker" / "lib"
sys.path.insert(0, str(LIB))

from host_base import CompletionResult  # noqa: E402
import table_round as tr  # noqa: E402


class FakeHost:
    """Records every call; THINK calls sleep so parallelism is observable."""

    def __init__(self, think_sleep=0.05, empty_for=None):
        self.calls = []
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.think_sleep = think_sleep
        self.empty_for = empty_for or set()
        self.n = 0

    def complete(self, *, model, system, user, num_predict, think=False, temperature=0.7):
        with self.lock:
            self.n += 1
            n = self.n
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append({"system": system, "user": user, "num_predict": num_predict,
                               "think": think})
        try:
            if "private intent" in system:
                time.sleep(self.think_sleep)
                text = f"INTENT-{n}-SECRET"
            elif "JSON" in system:
                text = '{"verdict": "pass", "retake_seat": null, "reason": "ok"}'
            elif "Game Master" in system:
                text = "The sigil is cracked."
            else:
                text = f"line-{n}"
            if any(tag in user for tag in self.empty_for):
                text = ""
            return CompletionResult(model=model, prompt_tokens=100, content_tokens=10,
                                    reasoning_tokens=50 if think else 0, ttft_s=0.01,
                                    total_s=0.02, content_text=text, reasoning_text="",
                                    finish_reason="stop" if text else "length",
                                    raw_usage={}, raw_extra={})
        finally:
            with self.lock:
                self.active -= 1


def _stages(res, name):
    return [s for s in res.stages if s.stage == name]


def test_two_pass_shape_and_parallel_think():
    host = FakeHost()
    res = tr.TableRoundProbe(host, "m").run("two_pass", 4)
    assert [s.stage for s in res.stages] == (
        ["gm_direction"] + ["think"] * 4 + ["speak"] * 4 + ["adjudication"])
    assert host.max_active >= 2            # THINK really ran concurrently
    think_calls = [c for c in host.calls if "private intent" in c["system"]]
    assert all(c["think"] for c in think_calls)
    speak_calls = [c for c in host.calls if "ONE spoken line" in c["system"]]
    assert speak_calls and not any(c["think"] for c in speak_calls)


def test_one_pass_shape_reasoning_on_every_seat():
    host = FakeHost()
    res = tr.TableRoundProbe(host, "m").run("one_pass", 4)
    assert [s.stage for s in res.stages] == ["gm_direction"] + ["seat_one_pass"] * 4 + ["adjudication"]
    assert not _stages(res, "think")
    seat_calls = [c for c in host.calls if "ONE spoken line" in c["system"]]
    assert all(c["think"] for c in seat_calls)
    assert host.max_active == 1


@pytest.mark.parametrize("shape", ["one_pass", "two_pass"])
def test_each_spoken_line_sees_every_previously_committed_line(shape):
    host = FakeHost()
    res = tr.TableRoundProbe(host, "m").run(shape, 4)
    spoken = [s for s in res.stages if s.stage in ("speak", "seat_one_pass")]
    speak_calls = [c for c in host.calls if "ONE spoken line" in c["system"]]
    for i, call in enumerate(speak_calls):
        for prev in spoken[:i]:
            assert prev.content in call["user"]
        for later in spoken[i + 1:]:
            assert later.content not in call["user"]


def test_private_intent_only_reaches_its_own_seat():
    host = FakeHost()
    res = tr.TableRoundProbe(host, "m").run("two_pass", 4)
    intents = {s.seat: s.content for s in _stages(res, "think")}
    speak_stages = _stages(res, "speak")
    speak_calls = [c for c in host.calls if "ONE spoken line" in c["system"]]
    for stage, call in zip(speak_stages, speak_calls):
        own = intents[stage.seat]
        assert own in call["user"]
        for seat, other in intents.items():
            if seat != stage.seat:
                assert other not in call["user"] and other not in call["system"]
    # never in the adjudication (spoken) transcript either
    adj = host.calls[-1]
    assert not any(i in adj["user"] for i in intents.values())


def test_think_runs_on_one_shared_snapshot():
    host = FakeHost()
    tr.TableRoundProbe(host, "m").run("two_pass", 4)
    think_users = [c["user"] for c in host.calls if "private intent" in c["system"]]
    assert len(set(think_users)) == 4 and all("(nothing said yet this round)" in u for u in think_users)


def test_seven_seats_cycle_cast_and_cap_brief():
    host = FakeHost()
    res = tr.TableRoundProbe(host, "m").run("two_pass", 7)
    assert len(_stages(res, "speak")) == 7
    for cast_id in tr.seat_cast(7):
        assert len(tr.brief_for(cast_id)[1]) <= tr.BRIEF_MAX_CHARS


def test_empty_content_is_recorded_as_failed_and_budget_capped():
    host = FakeHost(empty_for={"Your private intent"})
    res = tr.TableRoundProbe(host, "m").run("two_pass", 4)
    thinks = _stages(res, "think")
    assert all(not s.ok and s.budget_capped for s in thinks)
    summary = res.summary()
    assert summary["n_failed"] == 4 and summary["n_budget_capped"] == 4
    assert len(_stages(res, "speak")) == 4   # round continues without intent


def test_summary_fields():
    res = tr.TableRoundProbe(FakeHost(), "m").run("two_pass", 4)
    s = res.summary()
    for key in ("wall_s", "gm_direction_s", "think_pass_wall_s", "think_p95_s",
                "speak_p50_s", "adjudication_s", "reasoning_tokens"):
        assert s[key] is not None


def test_reasoning_budgets_passed_per_stage_when_set():
    class RecordingHost(FakeHost):
        def complete(self, *, model, system, user, num_predict, think=False,
                     temperature=0.7, thinking_token_budget=None):
            self.calls.append({"tb": thinking_token_budget, "system": system})
            return super().complete(model=model, system=system, user=user,
                                    num_predict=num_predict, think=think,
                                    temperature=temperature)

    host = RecordingHost()
    b = tr.Budgets(reasoning_gm=400, reasoning_think=200, reasoning_seat=250,
                   reasoning_speak=64, reasoning_adjudication=96)
    tr.TableRoundProbe(host, "m", b).run("two_pass", 4)
    tbs = [c["tb"] for c in host.calls if "tb" in c]
    assert tbs[0] == 400                                   # GM direction
    assert tbs.count(200) == 4                             # THINK
    assert tbs.count(64) == 4                              # SPEAK
    assert tbs[-1] == 96                                   # adjudication
    tr.TableRoundProbe(host, "m", b).run("one_pass", 4)
    assert [c["tb"] for c in host.calls if "tb" in c].count(250) == 4


def test_no_budget_means_no_kwarg():
    host = FakeHost()          # its complete() has no thinking_token_budget kwarg
    tr.TableRoundProbe(host, "m").run("two_pass", 4)  # must not raise TypeError
