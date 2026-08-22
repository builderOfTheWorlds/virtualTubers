"""
Layer 3 of the 3-layer offline content generator: the pass that turns a
segment's planned slots into actual voiced takes on disk.

This module owns almost no logic. `worklist` already decides what to
generate and under which conditions, `pool` already runs the workers with a
circuit breaker and a single writer thread, and `app/campaign/batch_generate`
already writes the take files and the manifest. This module is the wiring
plus one small take function.

Three things here are easy to get wrong and expensive to get wrong:

  - THE TAKE FUNCTION IS LOCAL, NOT `batch_generate._generate_take`. That one
    calls `update_context(scene=..., loop=take, carry={})` — it overwrites
    `loop` with the TAKE NUMBER and blanks `carry` unconditionally. `carry`
    is the only channel the arc's state reaches the model through, so reusing
    it would silently produce takes with no conditions at all, identical to
    their neutral siblings, with nothing in any log to say so.

  - `loop` COMES FROM THE BRIEF. It is the campaign's time-loop iteration,
    not a counter of retries.

  - THE NEUTRAL-TAKE GUARD IS HARD. Every ambient slot must end with exactly
    one take whose conditions are `{}`. A library missing one can dead-air at
    runtime, which is the single failure the whole `neutral_takes` design
    exists to prevent. It raises; it does not warn.
"""
import datetime
import glob
import logging
import pathlib

import yaml

import pool
import worklist
from campaign import batch_generate
from campaign.improviser import LLMImproviser
from campaign.pack import Scene

log = logging.getLogger(__name__)


class NeutralTakeError(RuntimeError):
    """Raised by the coverage guard. A distinct type so the API dispatcher
    can report it as a failed job with a readable reason rather than a
    generic crash."""
    pass


def generate_take(improviser, unit, config) -> list:
    """Produce ONE take for ONE WorkUnit.

    Returns a list of beat dicts; an empty list is a failed attempt.
    NEVER RAISES — catch every exception, log at ERROR, and return [].
    The pool treats both an empty list and an exception as a failure, but
    returning [] keeps the failure accounting in one place.
    """
    try:
        scene = Scene(
            id=unit.slot_id,
            prompt=unit.slot["prompt"],
            lore=unit.slot.get("lore", []),
        )
        improviser.recent = []
        improviser.update_context(
            scene=scene,
            loop=unit.slot.get("loop", 1),
            carry=unit.conditions,
        )
        beats = improviser.generate_scene(scene)
        return [batch_generate._beat_to_dict(b) for b in beats]
    except Exception as exc:
        log.error("generate_take failed for slot %s take %d: %s",
                  unit.slot_id, unit.take, exc)
        return []


def assert_neutral_take_coverage(segment_root, slot_ids) -> None:
    """For each slot id, read every take file under
    `worklist.take_path(segment_root, slot_id, n)` and confirm at least one
    has `conditions == {}` (an empty mapping, or a missing/None key — all
    three mean "unconditioned").

    Collect EVERY offending slot id, then raise ONE `NeutralTakeError`
    naming all of them. An operator fixing these wants the whole list, not
    one slot per run. A slot with no take files at all is an offender too.

    Discover the take files by globbing the slot's directory rather than
    counting up from 1 — a skipped leaf leaves gaps.
    """
    offenders = []
    for slot_id in slot_ids:
        slot_dir = segment_root / "slots" / slot_id
        has_neutral = False
        if slot_dir.exists():
            for path_str in glob.glob(str(slot_dir / "*.yaml")):
                path = pathlib.Path(path_str)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                except (OSError, yaml.YAMLError) as exc:
                    log.error("could not read take file %s: %s", path, exc)
                    continue
                if not isinstance(data, dict):
                    continue
                conditions = data.get("conditions")
                if conditions is None or conditions == {}:
                    has_neutral = True
                    break
        if not has_neutral:
            offenders.append(slot_id)

    if offenders:
        msg = "slots missing a neutral take: " + ", ".join(offenders)
        log.error(msg)
        raise NeutralTakeError(msg)


def generate_segment_dialogue(pack, segment_ids, config, llm, out_root,
                              *, progress=None, cancel_check=None):
    """The entry point. Returns an aggregate stats object or dict; must not
    return None on a successful run.

    For each segment id IN ORDER:
      1. Load `<out_root>/segments/<segment_id>/brief.yaml`.
      2. `units = worklist.build_worklist(out_root, [segment_id], config)`.
         THIS IS THE RESUME — re-running the scan is what makes the run
         resumable (design decision D8). Do NOT write your own
         "next unused take number" scan; that is precisely the racy call D8
         removed.
      3. Log the estimate from `worklist.estimate_seconds(units,
         config["dialogue"]["seconds_per_take"],
         config["dialogue"]["concurrency"])`.
      4. Call `pool.run_pool(...)` with:
           worker_factory = lambda: LLMImproviser(pack, llm)   ONE PER
                            WORKER THREAD — never one shared instance; it
                            holds mutable scene/carry/loop/recent state and
                            sharing it splices one slot's transcript into
                            another's prompt.
           generate       = lambda worker, unit: generate_take(worker, unit,
                                                               config)
           writer         = a closure calling
                            `batch_generate._write_take(...)` and
                            `batch_generate._append_manifest(...)`
           concurrency    = config["dialogue"]["concurrency"]
           max_attempts   = config["dialogue"]["max_attempts"]
           breaker        = pool.CircuitBreaker(**config["dialogue"]["breaker"])
           cancel_check   = the cancel_check argument, passed straight
                            through
      5. Call `progress(done, total, segment_id)` after each segment when
         `progress` is not None, where `done` counts segments finished so
         far and `total` is `len(segment_ids)`.
      6. Run `assert_neutral_take_coverage` for that segment's ambient slot
         ids.

    SPINE SLOTS ARE NEVER GENERATED. `kind: spine` is authored canon and a
    spine slot reaching the pool would overwrite it with improvised
    dialogue. `worklist.units_for_segment` should already exclude them —
    confirm that rather than assuming, and never add spine slots yourself.

    DO NOT BUILD AN LLM CLIENT. The caller passes `llm` already constructed
    and shared; this module must never call `concurrent_llm.from_profile`.
    One shared client with per-thread improvisers is the contract.

    `cancel_check` is forwarded to the pool UNCHANGED — the pool owns the
    polling policy and its own poller thread. Do not poll it here.
    """
    log.debug("generate_segment_dialogue called with %d segments", len(segment_ids))

    total_segments = len(segment_ids)
    total_written = 0
    total_failed = 0
    per_segment_counts = {}

    for done, segment_id in enumerate(segment_ids, start=1):
        log.debug("processing segment %s (%d/%d)", segment_id, done, total_segments)

        # 1. Load the brief
        segment_root = out_root / "segments" / segment_id
        brief_path = segment_root / "brief.yaml"
        try:
            with open(brief_path, "r", encoding="utf-8") as f:
                brief = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            log.error("could not load brief for segment %s: %s", segment_id, exc)
            raise

        # 2. Build the worklist (this is the resume)
        units = worklist.build_worklist(out_root, [segment_id], config)

        # 3. Log the estimate
        seconds_per_take = config["dialogue"]["seconds_per_take"]
        concurrency = config["dialogue"]["concurrency"]
        estimate = worklist.estimate_seconds(units, seconds_per_take, concurrency)
        log.info("segment %s: estimated %.1f seconds for %d units",
                 segment_id, estimate, len(units))

        # 4. Call pool.run_pool
        breaker = pool.CircuitBreaker(**config["dialogue"]["breaker"])

        def writer(unit, beats):
            generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            model = config["dialogue"].get("active_model", "")
            batch_generate._write_take(unit.path, unit.slot_id, unit.take,
                                       model, generated_at, beats)
            manifest_path = segment_root / "manifest.jsonl"
            batch_generate._append_manifest(manifest_path, {
                "segment_id": unit.segment_id,
                "slot_id": unit.slot_id,
                "take": unit.take,
                "conditions": unit.conditions,
                "beat_count": len(beats),
                "model": model,
                "timestamp": generated_at,
            })

        stats = pool.run_pool(
            units,
            worker_factory=lambda: LLMImproviser(pack, llm),
            generate=lambda worker, unit: generate_take(worker, unit, config),
            writer=writer,
            concurrency=concurrency,
            max_attempts=config["dialogue"]["max_attempts"],
            breaker=breaker,
            cancel_check=cancel_check,
        )

        total_written += stats.written
        total_failed += stats.failed
        per_segment_counts[segment_id] = {"written": stats.written, "failed": stats.failed}

        # 5. Report progress
        if progress is not None:
            progress(done, total_segments, segment_id)

        # 6. Run the neutral-take coverage guard
        ambient_slot_ids = [slot["slot_id"] for slot in brief.get("slots", [])
                            if slot.get("kind") != "spine"]
        assert_neutral_take_coverage(segment_root, ambient_slot_ids)

    log.info("generation complete: %d takes written, %d failures, per-segment: %s",
             total_written, total_failed, per_segment_counts)

    return {
        "written": total_written,
        "failed": total_failed,
        "per_segment": per_segment_counts,
    }
