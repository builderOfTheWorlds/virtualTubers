"""
Worker pool that drains the worklist for the 3-layer offline content generator.

This module is where three separate hazards meet. Each one is a real failure
that has a specific, non-obvious fix.

1. CONTEXT BLEED. `LLMImproviser` (app/campaign/improviser.py) is NOT
   thread-safe: it holds mutable `scene`, `carry`, `loop` and `recent`
   attributes that `update_context` rewrites on every take. Sharing one
   across eight workers splices one slot's transcript into another slot's
   prompt. The output is not a crash — it is takes that read as
   non-sequiturs, with nothing in the log to say why. The fix is ONE
   improviser per WORKER THREAD (not per unit — constructing one per unit
   would re-read the pack tens of thousands of times). The pack and the LLM
   client are read-only and stay shared.

2. INTERLEAVED WRITES. Every worker appending to the same manifest JSONL
   corrupts it. The fix is not a lock: workers RETURN results, and ONE
   dedicated writer thread performs every write. That also gives clean crash
   semantics — the file is only ever touched by one thread.

3. SILENT FAILURE. `LLMImproviser.generate_scene` catches EVERY exception and
   returns `[]`. A dead ollama, an OOM, or a wrong model name therefore
   produces an endless stream of empty takes AT FULL SPEED, and the run looks
   healthy the whole time. The fix is a rolling failure-rate circuit breaker
   that aborts the run. It is the difference between losing ten minutes and
   losing two days.
"""
import collections
import logging
import threading
import queue
from dataclasses import dataclass
from typing import List, Callable, Any, Optional

log = logging.getLogger(__name__)

# Poll interval for the cooperative cancellation check (v3, decision V11).
# 2.0s is deliberate: `cancel_check` is a database round-trip and a run lasts
# GPU-hours, so a sub-second interval would add thousands of pointless queries
# to buy cancellation latency nobody can perceive. Read from the module at
# call time — the test suite monkeypatches it.
CANCEL_POLL_SECONDS = 2.0


class PoolError(RuntimeError):
    """Raised for a nonsensical configuration (concurrency < 1, an invalid breaker window)."""
    pass


class CircuitBreakerTripped(PoolError):
    """Raised out of `run_pool` when the breaker trips. It is deliberately
    an exception and not a return value: the caller must not be able to
    mistake an aborted run for a completed one.
    """
    pass


class CircuitBreaker:
    def __init__(self, window: int, failure_rate: float, min_samples: int):
        if window < 1:
            raise PoolError("window must be at least 1")

        self.window = window
        self.failure_rate = failure_rate
        self.min_samples = min_samples
        self._outcomes = collections.deque(maxlen=window)
        self._lock = threading.Lock()

    def record(self, ok: bool) -> None:
        """Append one outcome. MUST be thread-safe: eight workers call it
        concurrently. Guard the deque with a `threading.Lock`.
        """
        with self._lock:
            self._outcomes.append(ok)

    @property
    def tripped(self) -> bool:
        """True when BOTH:
          - at least `min_samples` outcomes have been recorded in the
            current window, AND
          - the failure fraction within the window is >= `failure_rate`.
        Below `min_samples` it is always False. Three failures out of
        three is 100% and is not evidence; aborting a two-day run on it
        would be worse than the bug.
        Because the deque is bounded, old failures age out on their own —
        a rough patch at hour two must not abort a run that has since
        recovered.
        """
        with self._lock:
            if len(self._outcomes) < self.min_samples:
                return False

            failures = sum(1 for outcome in self._outcomes if not outcome)
            rate = failures / len(self._outcomes)
            return rate >= self.failure_rate


@dataclass
class PoolStats:
    planned: int
    written: int
    failed: int


def run_pool(units, *, worker_factory, generate, writer,
             concurrency=4, max_attempts=2, breaker=None,
             cancel_check=None) -> PoolStats:
    """
    Run the worker pool that drains the worklist for the 3-layer offline content generator.

    `units`        an ordered sequence of worklist.WorkUnit.
    `worker_factory()`  called ONCE PER WORKER THREAD, returns that
                   thread's private context object (in production, an
                   LLMImproviser). Never call it per unit, and never
                   share one result between threads.
    `generate(worker, unit)`  returns a list of beats. An empty list, or
                   any exception, is a FAILED attempt.
    `writer(unit, beats)`  called ONLY on the single writer thread.
    `concurrency`  number of worker threads. < 1 raises PoolError.
    `max_attempts` attempts per unit before it is counted failed.
                   Default 2 — a single transient timeout should not
                   cost a slot its take.
    `breaker`      a CircuitBreaker, or None to disable the check.
    `cancel_check` a zero-argument callable returning bool, or None to
                   disable. Polled on ONE dedicated thread — not per
                   worker, because in production it is a database query and
                   polling it from every worker at every unit boundary would
                   multiply that by `concurrency` for no extra fidelity.
                   When it returns True the pool sets `stop_event` and drains,
                   reusing the breaker's existing stop path, and RETURNS
                   partial stats. A cancel is a deliberate partial success:
                   it must not raise, must not touch `stats.failed`, and must
                   not trip the breaker.

    THE WORKER LOOP — implement exactly this. Each worker thread:

        worker = worker_factory()          # ONCE, before the loop
        while True:
            if stop_event.is_set(): break
            unit = work_queue.get()        # blocking, no timeout
            if unit is None: break         # its sentinel
            if stop_event.is_set(): break

            beats = None
            for attempt in range(1, max_attempts + 1):
                try:
                    result = generate(worker, unit)
                except Exception as exc:
                    log.error("unit %s attempt %d failed: %s",
                              unit.slot_id, attempt, exc)
                    continue                # next attempt
                if result:                  # non-empty == success
                    beats = result
                    break
                log.debug("unit %s attempt %d produced no beats",
                          unit.slot_id, attempt)

            ok = beats is not None
            if ok:
                results_queue.put((unit, beats))
            else:
                log.error("unit %s failed after %d attempts",
                          unit.slot_id, max_attempts)
                with stats_lock:
                    stats.failed += 1

            if breaker is not None:
                breaker.record(ok)          # EXACTLY ONCE PER UNIT
                if breaker.tripped:
                    stop_event.set()
                    break                   # do NOT raise in the thread

    Note the four things that loop gets right, each of which a naive
    version gets wrong:

      - `breaker.record(ok)` is called ONCE PER UNIT, after all attempts
        — not once per attempt. A unit that succeeds on retry is a
        success, and counting its first attempt as a failure would trip
        the breaker on a perfectly healthy run.
      - `stats.failed` is incremented at the point of failure. Do not
        leave it to be reconstructed later; there is no later.
      - A `generate` exception `continue`s to the next attempt rather
        than abandoning the unit, and NEVER escapes the worker. A worker
        that dies on its first bad unit silently shrinks the pool for the
        rest of the run.
      - The breaker sets `stop_event` and BREAKS. It does not raise
        inside the worker thread — an exception there cannot reach the
        caller and would only skip the thread's own cleanup. `run_pool`
        raises on the main thread after the joins.

    `stats.written` is incremented by the WRITER thread, not here — only
    a write that actually succeeded counts as written.

    Read only the breaker's public surface (`record`, `tripped`). Do not
    reach into its internals for the log message; log the unit count and
    the configured rate instead.

    Dispatch units IN THE GIVEN ORDER: fill the queue in order before
    starting any thread. Stopping the run at any point must leave a
    contiguous usable prefix of airtime rather than a scattered fraction
    that cannot air.

    THE WRITER THREAD: one thread, started before the workers, looping
    `item = results_queue.get()`, breaking on the `None` sentinel, else
    calling `writer(unit, beats)`. On success `stats.written += 1`; on
    exception, log at ERROR and `stats.failed += 1` — a full disk must
    not stall the run.

    THE ENDING — implement exactly this, in exactly this order, AFTER
    the joins (see the shutdown protocol in the notes):

        log.info("pool completed: %d written, %d failed",
                 stats.written, stats.failed)

        if breaker is not None and breaker.tripped:
            log.critical("circuit breaker tripped after %d units "
                         "(threshold %.2f)", stats.planned,
                         breaker.failure_rate)
            raise CircuitBreakerTripped(...)

        return stats

    The INFO line comes FIRST, on every path — including the aborted
    one. Do not put it on the success path only. An abort is precisely
    the case where the operator most needs the count: they come back to
    a run that stopped early and the first question is "how much of the
    week is usable?" A partial arc of real takes is salvageable; zero is
    a restart. Logging the summary only when nothing went wrong answers
    that question exactly when it did not need asking, and stays silent
    when it did.

    CRITICAL, not ERROR, for the trip: ERROR is already the noise floor
    of a failing run, and only CRITICAL distinguishes "the run gave up"
    from "one slot misbehaved".

    `stats.written` and `stats.failed` are touched from several threads.
    Guard every mutation with a single `threading.Lock`.
    """
    if concurrency < 1:
        raise PoolError("concurrency must be at least 1")

    log.info("starting pool with %d units, %d workers", len(units), concurrency)

    # Setup queues and shared state
    work_queue = queue.Queue()
    results_queue = queue.Queue()
    stop_event = threading.Event()
    stats = PoolStats(planned=len(units), written=0, failed=0)
    stats_lock = threading.Lock()

    # Fill the work queue in order
    for unit in units:
        work_queue.put(unit)

    # Add sentinels for workers
    for _ in range(concurrency):
        work_queue.put(None)

    # Start writer thread first
    def writer_thread():
        while True:
            item = results_queue.get()
            if item is None:
                break

            unit, beats = item
            try:
                writer(unit, beats)
                with stats_lock:
                    stats.written += 1
            except Exception as exc:
                log.error("failed to write unit %s: %s", unit.slot_id, exc)
                with stats_lock:
                    stats.failed += 1

    writer_thread_obj = threading.Thread(target=writer_thread)
    writer_thread_obj.start()

    # Start worker threads
    def worker_thread():
        worker = worker_factory()
        while True:
            if stop_event.is_set():
                break

            try:
                unit = work_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if unit is None:
                break

            if stop_event.is_set():
                break

            beats = None
            for attempt in range(1, max_attempts + 1):
                try:
                    result = generate(worker, unit)
                except Exception as exc:
                    log.error("unit %s attempt %d failed: %s",
                              unit.slot_id, attempt, exc)
                    continue

                if result:  # non-empty == success
                    beats = result
                    break

                log.debug("unit %s attempt %d produced no beats",
                          unit.slot_id, attempt)

            ok = beats is not None
            if ok:
                results_queue.put((unit, beats))
            else:
                log.error("unit %s failed after %d attempts",
                          unit.slot_id, max_attempts)
                with stats_lock:
                    stats.failed += 1

            if breaker is not None:
                breaker.record(ok)  # EXACTLY ONCE PER UNIT
                if breaker.tripped:
                    stop_event.set()
                    break  # do NOT raise in the thread

    threads = []
    for _ in range(concurrency):
        thread = threading.Thread(target=worker_thread)
        thread.start()
        threads.append(thread)

    # Cooperative cancellation. Checks BEFORE its first wait: the flag can
    # already be set when the pool starts, because the operator cancelled
    # while the job was still queued, and sleeping first would burn a full
    # interval of GPU on work that was already called off.
    canceller = None
    if cancel_check is not None:
        poll_done = threading.Event()

        def cancel_poller():
            while True:
                try:
                    if cancel_check():
                        log.info("cancellation requested; stopping pool")
                        stop_event.set()
                        return
                except Exception as exc:
                    # A transient DB blip must not abort six hours of work,
                    # and must not kill this thread either — after which a
                    # real cancel minutes later would never be seen.
                    log.error("cancel_check raised, continuing: %s", exc)
                if poll_done.wait(CANCEL_POLL_SECONDS):
                    return

        canceller = threading.Thread(target=cancel_poller, daemon=True)
        canceller.start()

    # Wait for all workers to finish
    for thread in threads:
        thread.join()

    # Close writer thread
    results_queue.put(None)
    writer_thread_obj.join()

    # Daemon, so a hung join can never wedge process exit — but still joined:
    # a leaked poller keeps querying the database once per completed job for
    # the life of the service.
    if canceller is not None:
        poll_done.set()
        canceller.join(timeout=1.0)

    log.info("pool completed: %d written, %d failed",
             stats.written, stats.failed)

    if breaker is not None and breaker.tripped:
        log.critical("circuit breaker tripped after %d units "
                     "(threshold %.2f)", stats.planned,
                     breaker.failure_rate)
        raise CircuitBreakerTripped("Circuit breaker tripped")

    return stats
