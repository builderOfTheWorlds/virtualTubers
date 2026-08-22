"""Acceptance tests for src/pool.py — the worker pool that drains the worklist.

Three separate hazards meet in this module, and each has a test section below.

1. CONTEXT BLEED. `LLMImproviser` is not thread-safe: it holds mutable
   `scene`/`carry`/`loop`/`recent` that `update_context` rewrites. Sharing one
   across eight workers would splice one slot's transcript into another's
   prompt — producing takes that read as non-sequiturs, with nothing in the
   logs to say why. One improviser per worker thread; the read-only pack and
   the single LLM client are shared.

2. INTERLEAVED WRITES. Every worker appending to the same manifest JSONL
   corrupts it. Workers return results; ONE writer thread performs every write.

3. SILENT FAILURE. `generate_scene` catches every exception and returns `[]`,
   so a dead ollama, an OOM, or a wrong model name produces an endless stream
   of empty takes at full speed. Without a circuit breaker that is the
   difference between losing ten minutes and losing two days.
"""
import pathlib
import threading
import time

import pytest

import pool
from worklist import WorkUnit


# A deadlocked pool must FAIL a test, not hang the suite. Every hazard this
# module guards against (a worker blocked on an empty queue, a writer thread
# never joined, a breaker that stops dispatch while workers still wait on a
# sentinel that never arrives) presents as a hang rather than an error, and a
# hang gives whoever is reading the output nothing to act on.
pytestmark = pytest.mark.timeout(30)


def unit(slot_id="s-001", take=1, segment_id="seg-001", root=None):
    root = root or pathlib.Path("/tmp/nonexistent")
    return WorkUnit(segment_id=segment_id, slot_id=slot_id, take=take,
                    conditions={}, path=root / f"{slot_id}-{take:03d}.yaml",
                    slot={"slot_id": slot_id, "kind": "ambient", "prompt": "x"})


def units(count, root=None):
    return [unit(slot_id=f"s-{n:03d}", take=1, root=root) for n in range(count)]


BEATS = [{"speaker": "helen", "text": "The fire is low."}]


class Recorder:
    """Collects what the writer was handed, and on which thread."""

    def __init__(self):
        self.writes = []
        self.threads = set()
        self._lock = threading.Lock()

    def __call__(self, work_unit, beats):
        with self._lock:
            self.writes.append((work_unit.slot_id, work_unit.take))
            self.threads.add(threading.current_thread().name)


def always_ok(worker, work_unit):
    return BEATS


# ── the basic contract ────────────────────────────────────────────────────────

def test_every_unit_is_generated_and_written():
    writer = Recorder()
    stats = pool.run_pool(units(12), worker_factory=object, generate=always_ok,
                          writer=writer, concurrency=4)
    assert len(writer.writes) == 12
    assert stats.written == 12
    assert stats.failed == 0


def test_an_empty_worklist_is_not_an_error():
    """A fully-generated library re-scanned yields nothing to do."""
    writer = Recorder()
    stats = pool.run_pool([], worker_factory=object, generate=always_ok,
                          writer=writer, concurrency=4)
    assert stats.written == 0
    assert writer.writes == []


def test_the_generate_callable_receives_the_unit_it_is_writing():
    seen = []
    writer = Recorder()

    def generate(worker, work_unit):
        seen.append(work_unit.slot_id)
        return BEATS

    pool.run_pool(units(5), worker_factory=object, generate=generate,
                  writer=writer, concurrency=2)
    assert sorted(seen) == sorted(u.slot_id for u in units(5))


def test_stats_report_the_planned_total():
    stats = pool.run_pool(units(7), worker_factory=object, generate=always_ok,
                          writer=Recorder(), concurrency=3)
    assert stats.planned == 7


# ── 1. no context bleed: one improviser per worker ────────────────────────────

def test_worker_factory_is_called_once_per_worker_not_once_per_unit():
    """Constructing an improviser per unit would work but re-reads the pack
    50,000 times; constructing one for all of them is the bug."""
    calls = []
    pool.run_pool(units(40), worker_factory=lambda: calls.append(1) or object(),
                  generate=always_ok, writer=Recorder(), concurrency=4)
    assert len(calls) == 4


def test_each_worker_object_is_only_ever_used_by_one_thread():
    """The load-bearing invariant. If one improviser reaches two threads, one
    slot's transcript splices into another's prompt and the takes read as
    non-sequiturs, with nothing in the log to explain it."""
    owners = {}
    violations = []
    lock = threading.Lock()

    def generate(worker, work_unit):
        with lock:
            owner = owners.setdefault(id(worker), threading.current_thread().name)
            if owner != threading.current_thread().name:
                violations.append((id(worker), owner))
        return BEATS

    pool.run_pool(units(60), worker_factory=object, generate=generate,
                  writer=Recorder(), concurrency=4)
    assert violations == []


def test_distinct_worker_objects_are_handed_out():
    """Each worker thread gets its OWN object from worker_factory.

    In the real run that object is an LLMImproviser holding mutable
    per-scene state; two threads sharing one would interleave their
    conversations into each other's context.

    The barrier is what makes this assertion mean something. Without it
    the test is a race it usually loses: threads are started in sequence
    and this generate returns instantly, so worker 1 can drain the whole
    queue before worker 2 is ever scheduled, and a pool that correctly
    built four objects still only shows one to `seen`. Blocking every
    worker inside generate until all `concurrency` of them have arrived
    forces all four to be simultaneously in flight, which is the only
    condition under which "they don't share an object" is a claim about
    anything.
    """
    seen = set()
    lock = threading.Lock()
    all_in_flight = threading.Barrier(4, timeout=10)

    def generate(worker, work_unit):
        with lock:
            seen.add(id(worker))
        try:
            all_in_flight.wait()
        except threading.BrokenBarrierError:  # pragma: no cover - only on failure
            pass
        return BEATS

    pool.run_pool(units(40), worker_factory=object, generate=generate,
                  writer=Recorder(), concurrency=4)
    assert len(seen) == 4


def test_workers_really_run_concurrently():
    """A barrier that never fills means the pool is serialising, which would
    turn a 52-hour run back into a 262-hour one."""
    barrier = threading.Barrier(4, timeout=10)

    def generate(worker, work_unit):
        barrier.wait()
        return BEATS

    pool.run_pool(units(8), worker_factory=object, generate=generate,
                  writer=Recorder(), concurrency=4)


# ── 2. single writer ──────────────────────────────────────────────────────────

def test_every_write_happens_on_exactly_one_thread():
    """No locks, no interleaved JSONL, clean crash semantics."""
    writer = Recorder()
    pool.run_pool(units(40), worker_factory=object, generate=always_ok,
                  writer=writer, concurrency=8)
    assert len(writer.threads) == 1


def test_writes_do_not_happen_on_a_worker_thread():
    worker_threads = set()
    writer = Recorder()

    def generate(worker, work_unit):
        worker_threads.add(threading.current_thread().name)
        return BEATS

    pool.run_pool(units(40), worker_factory=object, generate=generate,
                  writer=writer, concurrency=4)
    assert writer.threads.isdisjoint(worker_threads)


def test_no_write_is_lost_when_the_pool_finishes():
    """The writer thread must be drained and joined, not abandoned."""
    writer = Recorder()
    pool.run_pool(units(200), worker_factory=object, generate=always_ok,
                  writer=writer, concurrency=8)
    assert len(writer.writes) == 200


def test_a_failing_write_is_counted_and_does_not_stall_the_run():
    calls = []

    def writer(work_unit, beats):
        calls.append(work_unit.slot_id)
        if len(calls) == 2:
            raise OSError("disk full")

    stats = pool.run_pool(units(6), worker_factory=object, generate=always_ok,
                          writer=writer, concurrency=2)
    assert len(calls) == 6
    assert stats.failed == 1


# ── dispatch order: a contiguous prefix, not a scattered fraction ─────────────

def test_units_are_dispatched_in_worklist_order():
    """Stopping the run at any point must leave a contiguous usable prefix of
    airtime. Shuffling costs nothing in throughput and everything in that."""
    dispatched = []
    lock = threading.Lock()

    def generate(worker, work_unit):
        with lock:
            dispatched.append(work_unit.slot_id)
        return BEATS

    ordered = units(30)
    pool.run_pool(ordered, worker_factory=object, generate=generate,
                  writer=Recorder(), concurrency=1)
    assert dispatched == [u.slot_id for u in ordered]


# ── 3. failure accounting ─────────────────────────────────────────────────────

def test_an_exception_in_generate_is_counted_not_propagated():
    """One bad slot must not tear down a two-day run."""
    def generate(worker, work_unit):
        if work_unit.slot_id == "s-002":
            raise RuntimeError("model said no")
        return BEATS

    stats = pool.run_pool(units(6), worker_factory=object, generate=generate,
                          writer=Recorder(), concurrency=2)
    assert stats.failed == 1
    assert stats.written == 5


def test_an_exception_does_not_kill_the_worker_thread():
    """A worker that dies on the first bad unit silently shrinks the pool."""
    def generate(worker, work_unit):
        raise RuntimeError("always fails")

    stats = pool.run_pool(units(20), worker_factory=object, generate=generate,
                          writer=Recorder(), concurrency=4, breaker=None)
    assert stats.failed == 20


def test_empty_beats_count_as_a_failure_and_are_not_written():
    """`generate_scene` returns [] for EVERY failure — timeout, OOM, wrong
    model name. An empty take written to disk is indistinguishable from a real
    one until someone tries to air it."""
    writer = Recorder()
    stats = pool.run_pool([unit()], worker_factory=object,
                          generate=lambda w, u: [], writer=writer,
                          concurrency=1, breaker=None)
    assert stats.failed == 1
    assert stats.written == 0
    assert writer.writes == []


def test_a_failure_is_logged_at_error_with_the_unit_that_failed(caplog):
    caplog.set_level("ERROR")
    pool.run_pool([unit(slot_id="s-042")], worker_factory=object,
                  generate=lambda w, u: (_ for _ in ()).throw(RuntimeError("boom")),
                  writer=Recorder(), concurrency=1, breaker=None)
    assert any("s-042" in record.getMessage() for record in caplog.records)


# ── retries ───────────────────────────────────────────────────────────────────

def test_a_unit_is_retried_up_to_max_attempts_before_being_counted_failed():
    attempts = []

    def generate(worker, work_unit):
        attempts.append(1)
        return []

    stats = pool.run_pool([unit()], worker_factory=object, generate=generate,
                          writer=Recorder(), concurrency=1, max_attempts=3,
                          breaker=None)
    assert len(attempts) == 3
    assert stats.failed == 1


def test_a_retry_that_succeeds_is_written_and_not_counted_as_a_failure():
    attempts = []

    def generate(worker, work_unit):
        attempts.append(1)
        return BEATS if len(attempts) > 1 else []

    writer = Recorder()
    stats = pool.run_pool([unit()], worker_factory=object, generate=generate,
                          writer=writer, concurrency=1, max_attempts=3)
    assert len(attempts) == 2
    assert stats.written == 1
    assert stats.failed == 0


def test_max_attempts_defaults_to_at_least_two():
    """A single transient timeout should not cost a slot its take."""
    attempts = []
    pool.run_pool([unit()], worker_factory=object,
                  generate=lambda w, u: attempts.append(1) or [],
                  writer=Recorder(), concurrency=1, breaker=None)
    assert len(attempts) >= 2


# ── the circuit breaker ───────────────────────────────────────────────────────

def test_a_healthy_run_never_trips_the_breaker():
    breaker = pool.CircuitBreaker(window=20, failure_rate=0.5, min_samples=10)
    pool.run_pool(units(100), worker_factory=object, generate=always_ok,
                  writer=Recorder(), concurrency=4, breaker=breaker)
    assert breaker.tripped is False


def test_the_breaker_holds_until_min_samples_are_seen():
    """Three failures out of three is 100%, but three is not evidence."""
    breaker = pool.CircuitBreaker(window=20, failure_rate=0.5, min_samples=10)
    for _ in range(3):
        breaker.record(False)
    assert breaker.tripped is False


def test_the_breaker_trips_once_the_rate_is_exceeded():
    breaker = pool.CircuitBreaker(window=20, failure_rate=0.5, min_samples=10)
    for _ in range(10):
        breaker.record(False)
    assert breaker.tripped is True


def test_the_breaker_is_rolling_so_early_failures_age_out():
    """A rough patch at hour two must not abort a run that has since
    recovered."""
    breaker = pool.CircuitBreaker(window=10, failure_rate=0.5, min_samples=10)
    for _ in range(5):
        breaker.record(False)
    for _ in range(10):
        breaker.record(True)
    assert breaker.tripped is False


def test_a_total_outage_aborts_the_run_instead_of_burning_two_days():
    """The whole point: a dead ollama yields empty takes at full speed."""
    attempted = []
    lock = threading.Lock()

    def generate(worker, work_unit):
        with lock:
            attempted.append(work_unit.slot_id)
        return []

    breaker = pool.CircuitBreaker(window=20, failure_rate=0.5, min_samples=10)
    with pytest.raises(pool.CircuitBreakerTripped):
        pool.run_pool(units(500), worker_factory=object, generate=generate,
                      writer=Recorder(), concurrency=4, max_attempts=1,
                      breaker=breaker)
    assert len(attempted) < 500, "the pool kept going after the breaker tripped"


def test_tripping_logs_at_critical(caplog):
    """This is the one message an operator must find when they come back to a
    run that stopped early. ERROR is already the noise floor of a failing run —
    only CRITICAL distinguishes "it gave up" from "a slot misbehaved"."""
    caplog.set_level("DEBUG")
    breaker = pool.CircuitBreaker(window=20, failure_rate=0.5, min_samples=10)
    with pytest.raises(pool.CircuitBreakerTripped):
        pool.run_pool(units(200), worker_factory=object,
                      generate=lambda w, u: [], writer=Recorder(),
                      concurrency=2, max_attempts=1, breaker=breaker)
    assert any(record.levelname == "CRITICAL" for record in caplog.records)


def test_an_aborted_run_still_reports_what_it_wrote(caplog):
    """The completion summary must be logged BEFORE the raise, not after it.

    An abort is precisely the case where the operator most needs the count.
    They come back to a run that stopped early and the first question is
    "how much of the week is usable?" — a partial arc of real takes is
    salvageable, zero is a restart. Logging the summary only on the success
    path answers that question exactly when it does not need asking, and
    stays silent when it does.
    """
    caplog.set_level("DEBUG")
    generated = []
    written = []

    def generate(worker, work_unit):
        # A short healthy prefix, then a total outage. Count OUR OWN calls:
        # gating on len(written) would race the writer thread, which lags far
        # enough behind an instant generate to never reach the threshold.
        generated.append(work_unit)
        if len(generated) <= 5:
            return BEATS
        return []

    def writer(work_unit, beats):
        written.append(work_unit)

    breaker = pool.CircuitBreaker(window=20, failure_rate=0.5, min_samples=10)
    with pytest.raises(pool.CircuitBreakerTripped):
        pool.run_pool(units(200), worker_factory=object, generate=generate,
                      writer=writer, concurrency=1, max_attempts=1,
                      breaker=breaker)

    summaries = [r for r in caplog.records
                 if r.levelname == "INFO" and "written" in r.getMessage()]
    assert summaries, "aborted run logged no completion summary"


def test_work_completed_before_the_breaker_tripped_is_still_written():
    """An abort must not discard the hours of good takes that preceded it."""
    calls = []
    lock = threading.Lock()
    writer = Recorder()

    def generate(worker, work_unit):
        with lock:
            calls.append(1)
            n = len(calls)
        return BEATS if n <= 20 else []

    breaker = pool.CircuitBreaker(window=20, failure_rate=0.5, min_samples=10)
    with pytest.raises(pool.CircuitBreakerTripped):
        pool.run_pool(units(400), worker_factory=object, generate=generate,
                      writer=writer, concurrency=2, max_attempts=1,
                      breaker=breaker)
    assert len(writer.writes) >= 20


def test_the_breaker_is_safe_under_concurrent_records():
    breaker = pool.CircuitBreaker(window=1000, failure_rate=0.99, min_samples=10)

    def hammer():
        for _ in range(500):
            breaker.record(True)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert breaker.tripped is False


def test_breaker_rejects_a_nonsensical_configuration():
    with pytest.raises(pool.PoolError):
        pool.CircuitBreaker(window=0, failure_rate=0.5, min_samples=10)


# ── configuration guards ──────────────────────────────────────────────────────

def test_a_concurrency_below_one_is_rejected():
    with pytest.raises(pool.PoolError):
        pool.run_pool(units(2), worker_factory=object, generate=always_ok,
                      writer=Recorder(), concurrency=0)


# ---------------------------------------------------------------------------
# 4. COOPERATIVE CANCELLATION (v3 / decision V11)
#
# A generation run is GPU-hours long. Without a way in, an operator who
# cancels a six-hour job watches it burn the GPU to completion anyway. The
# breaker already owns the "stop early and drain" machinery; cancellation
# reuses it rather than inventing a second stop path.
#
# `cancel_check` is polled on ONE dedicated thread, not per worker: in
# production it is a database query, and polling it from every worker at every
# unit boundary would multiply that by `concurrency` for no extra fidelity.
# ---------------------------------------------------------------------------

@pytest.fixture
def fast_poll(monkeypatch):
    """Production polls every 2 s — one cheap DB query against a run measured
    in GPU-hours. Tests cannot wait that long, so shrink the interval and give
    `generate` a small cost so units do not all drain before the first tick.
    A pool whose units are free is not a pool this feature is for."""
    monkeypatch.setattr(pool, "CANCEL_POLL_SECONDS", 0.02)


def slow_generate(_worker, _unit):
    time.sleep(0.01)
    return BEATS


def test_cancel_check_defaults_to_none_and_changes_nothing():
    recorder = Recorder()
    stats = pool.run_pool(units(8), worker_factory=lambda: object(),
                          generate=lambda w, u: BEATS, writer=recorder,
                          concurrency=2)
    assert stats.written == 8
    assert stats.failed == 0


def test_cancel_check_stops_the_run_early(fast_poll):
    recorder = Recorder()
    stats = pool.run_pool(units(200), worker_factory=lambda: object(),
                          generate=slow_generate, writer=recorder,
                          concurrency=2, cancel_check=lambda: True)

    # planned still reports the whole batch — the operator needs to know how
    # much of the week was NOT produced, not just what was.
    assert stats.planned == 200
    assert stats.written < 200


def test_cancel_returns_partial_stats_and_does_not_raise(fast_poll):
    """A cancel is a deliberate partial success, not a failure. Raising here
    would make the dispatcher record `status=failed` for something the
    operator asked for on purpose."""
    recorder = Recorder()
    stats = pool.run_pool(units(200), worker_factory=lambda: object(),
                          generate=slow_generate, writer=recorder,
                          concurrency=2, cancel_check=lambda: True)
    assert isinstance(stats, pool.PoolStats)
    assert stats.written == len(recorder.writes)


def test_an_already_cancelled_job_stops_before_doing_real_work():
    """The flag can already be set when the pool starts — the operator
    cancelled while the job sat queued. The first poll must happen BEFORE the
    first wait, or the pool burns a full interval of GPU on work that was
    already called off."""
    recorder = Recorder()
    stats = pool.run_pool(units(200), worker_factory=lambda: object(),
                          generate=slow_generate, writer=recorder,
                          concurrency=2, cancel_check=lambda: True)
    assert stats.written < 200


def test_cancel_check_returning_false_never_stops_the_run(fast_poll):
    recorder = Recorder()
    stats = pool.run_pool(units(12), worker_factory=lambda: object(),
                          generate=lambda w, u: BEATS, writer=recorder,
                          concurrency=3, cancel_check=lambda: False)
    assert stats.written == 12
    assert stats.failed == 0


def test_cancel_check_is_actually_polled():
    calls = []

    def check():
        calls.append(1)
        return False

    pool.run_pool(units(6), worker_factory=lambda: object(),
                  generate=lambda w, u: BEATS, writer=Recorder(),
                  concurrency=2, cancel_check=check)
    assert calls, "cancel_check was never called"


def test_a_raising_cancel_check_does_not_cancel_or_kill_the_run(fast_poll):
    """A transient DB blip must not abort six hours of work, and must not
    take the poller thread down either — after which a real cancel would
    never be seen."""
    recorder = Recorder()
    stats = pool.run_pool(units(12), worker_factory=lambda: object(),
                          generate=slow_generate, writer=recorder,
                          concurrency=2,
                          cancel_check=lambda: (_ for _ in ()).throw(
                              RuntimeError("postgres went away")))
    assert stats.written == 12
    assert stats.failed == 0


def test_poller_thread_exits_when_the_pool_finishes(fast_poll):
    """A leaked daemon poller would keep querying the database forever, once
    per completed job, for the life of the service."""
    calls = []

    def check():
        calls.append(1)
        return False

    pool.run_pool(units(4), worker_factory=lambda: object(),
                  generate=lambda w, u: BEATS, writer=Recorder(),
                  concurrency=2, cancel_check=check)

    settled = len(calls)
    time.sleep(0.3)          # many poll intervals
    assert len(calls) == settled, "poller kept running after run_pool returned"


def test_cancel_does_not_trip_the_breaker_or_raise_it(fast_poll):
    """Cancelling is not a failure signal — a cancelled run must not be
    reported as a tripped circuit breaker."""
    breaker = pool.CircuitBreaker(window=20, failure_rate=0.5, min_samples=2)
    stats = pool.run_pool(units(200), worker_factory=lambda: object(),
                          generate=slow_generate, writer=Recorder(),
                          concurrency=2, breaker=breaker,
                          cancel_check=lambda: True)
    assert isinstance(stats, pool.PoolStats)
    assert not breaker.tripped
