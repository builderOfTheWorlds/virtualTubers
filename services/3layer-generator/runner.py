"""
runner.py
Dispatcher for the 3-layer generator's API service.

Turns a queued job row into GPU work: claims one job at a time, drives the
right layer function, mirrors what that function wrote to disk into Postgres,
and records the outcome on the job row.

The unit of work is `dispatch_once(ctx)`, a plain synchronous call that claims
at most one job and returns whether it did. `run_forever` is a thin loop over
it, and the API's lifespan starts that loop in a thread. Everything interesting
is reachable without concurrency, which is what makes this module testable on
a machine that is also running a 70B model.

Dependencies are injected through `Context` so tests can substitute fakes for
the store, the config, and the three layer functions. In production
`build_default_context()` wires the real ones.
"""
import dataclasses
import datetime
import logging
import math
import pathlib

import yaml

log = logging.getLogger(__name__)


@dataclasses.dataclass
class Context:
    """Mutable bundle of everything the dispatcher needs.

    Tests reassign `ctx.plan_arc` / `ctx.plan_segment` to inject failures, so
    this is deliberately not frozen. `_calls` is a test-only escape hatch that
    defaults to None so production construction never has to pass it.
    """
    config: dict
    pack_root: pathlib.Path
    output_root: pathlib.Path
    store: object
    build_llm: object
    load_pack: object
    build_vocab: object
    plan_arc: object
    plan_segment: object
    generate_dialogue: object
    _calls: object = None


def build_default_context(config, pack_root, output_root) -> Context:
    """Wire the real dependencies.

    `config` is the loaded generation.yaml DICT, which shadows the config
    MODULE of the same name. The module is imported under an alias and the
    layer modules are imported inside this function (not at module top level)
    so a missing layer module fails the one job that needs it, not the whole
    service.
    """
    import config as config_module
    import concurrent_llm

    import campaign.pack
    import vocabulary
    import plan_arc
    import plan_segment
    import generate_segment_dialogue

    def build_llm(profile_name, layer):
        resolved = config_module.resolve_profile(
            config, layer, profile_name or None)
        return concurrent_llm.from_profile(resolved)

    return Context(
        config=config,
        pack_root=pathlib.Path(pack_root),
        output_root=pathlib.Path(output_root),
        store=__import__("generation_store"),
        build_llm=build_llm,
        load_pack=campaign.pack.load_pack,
        build_vocab=vocabulary.Vocabulary.from_config_and_pack,
        plan_arc=plan_arc.plan_arc,
        plan_segment=plan_segment.plan_segment,
        generate_dialogue=generate_segment_dialogue.generate_segment_dialogue,
    )


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string. `datetime.utcnow()` is
    deprecated in 3.12 and the service image is python:3.12-slim."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class EmptyOutputError(RuntimeError):
    """A stage ran without raising but produced nothing usable.

    plan_arc deliberately skips a batch it cannot plan rather than failing,
    so a run where every batch was skipped returns normally and writes an
    arc_plan.yaml with an empty segment list. Reporting that job `completed`
    hides a total failure behind a green status, so the dispatcher raises
    this instead and lets the normal error path mark the job failed.
    """


def _expected_segment_count(config):
    """How many segments a complete arc plan should hold, or None when the
    config does not say.

    Mirrors arc_schema.n_segments deliberately rather than importing it: the
    dispatcher keeps the layer modules out of its own import graph so a
    missing one fails only the job that needs it (see build_default_context).
    """
    arc = config.get("arc") or {}
    total_hours = arc.get("hours_total")
    segment_hours = arc.get("segment_hours")
    if not total_hours or not segment_hours or segment_hours <= 0:
        log.debug("_expected_segment_count: arc hours not configured")
        return None
    return math.ceil(total_hours / segment_hours)


def artifact_path(ctx, run, kind, segment_id) -> pathlib.Path:
    """Where an artifact of each kind lives in the working directory,
    namespaced under `run` (the output namespace a job writes to) — NOT
    `pack` (the source pack cast/scenes/lore are loaded from). The two
    happen to be equal for a job with no `run` on its row, which is exactly
    what the `job.get("run") or job["pack"]` fallback in `dispatch_once`
    arranges."""
    base = pathlib.Path(ctx.output_root) / run
    if kind == "arc_plan":
        return base / "arc_plan.yaml"
    seg = base / "segments" / segment_id
    if kind == "brief":
        return seg / "brief.yaml"
    if kind == "tree":
        return seg / "tree.yaml"
    if kind == "dialogue":
        return seg / "dialogue.yaml"
    raise ValueError(f"unknown artifact kind {kind!r}")


def _mirror_dialogue_takes(ctx, run, segment_id, job_id) -> int:
    """Mirror every generated take file for one segment into the store.

    Dialogue takes land at
    `<output_root>/<run>/segments/<segment_id>/slots/<slot_id>/<take>.yaml`
    — one file per (slot, take), NOT the single `dialogue.yaml` per segment
    `artifact_path` knows how to build for the other three kinds. Each take
    is mirrored under its own natural key so the Data Viewer can list and
    open individual takes; `segment_id` here is
    `<segment_id>/<slot_id>/<take>` to keep every take's key unique within
    the (pack, kind, segment_id) constraint.

    Returns how many take files were mirrored."""
    slots_root = pathlib.Path(ctx.output_root) / run / "segments" / segment_id / "slots"
    if not slots_root.is_dir():
        return 0
    count = 0
    for slot_dir in sorted(slots_root.iterdir()):
        if not slot_dir.is_dir():
            continue
        for take_file in sorted(slot_dir.glob("*.yaml")):
            with open(take_file, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f)
            key = f"{segment_id}/{slot_dir.name}/{take_file.stem}"
            ctx.store.upsert_artifact(run, "dialogue", key, content, job_id)
            count += 1
    return count


def _mirror(ctx, run, kind, segment_id, job_id) -> bool:
    """Read an artifact file back and upsert it into the store, keyed on
    `run` (the output namespace), not `pack`.

    Returns True when a file was read and mirrored, False when the file does
    not exist (a skipped leaf writes nothing and that is not an error).
    """
    path = artifact_path(ctx, run, kind, segment_id)
    if not path.exists():
        log.debug("mirror: %s missing, skipping", path)
        return False
    with open(path, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    ctx.store.upsert_artifact(run, kind, segment_id, content, job_id)
    log.debug("mirror: upserted %s:%s", kind, segment_id)
    return True


def _make_progress(ctx, job_id):
    """A progress callback closure over the job id."""
    def progress(done, total, node):
        if isinstance(node, dict):
            last_node = node.get("node_id")
        else:
            last_node = str(node)
        ctx.store.update_progress(job_id, {
            "done": done,
            "total": total,
            "last_node": last_node,
            "updated_at": _utc_now_iso(),
        })
    return progress


def _make_llm_progress(ctx, job_id):
    """A live token-decode progress callback closure over the job id, for
    the arc stage's single sequential LLM call (see plan_arc's
    on_llm_progress / concurrent_llm.PooledOllamaClient.complete_streaming).
    Distinct from _make_progress: this describes progress WITHIN one LLM
    call (tokens decoded so far), not across work units.

    Best-effort: an update_llm_progress failure (e.g. a Postgres blip) must
    never abort the arc-planning call it is merely reporting on, so it is
    logged and swallowed rather than raised.
    """
    def on_llm_progress(snapshot):
        try:
            ctx.store.update_llm_progress(job_id, snapshot)
        except Exception:
            log.warning("_make_llm_progress: failed to write progress for job_id=%s",
                        job_id, exc_info=True)
    return on_llm_progress


def _make_cancel_check(ctx, job_id):
    """A cancel-check closure over the job id."""
    def cancel_check():
        return ctx.store.is_cancelled(job_id)
    return cancel_check


def _arc_segments(ctx, run_name) -> list:
    """The segment list from the arc plan, or [] when absent."""
    path = artifact_path(ctx, run_name, "arc_plan", "")
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict):
        return []
    segments = doc.get("segments")
    return segments if isinstance(segments, list) else []


def _select_segments(ctx, run_name, job) -> list:
    """Resolve which arc segments this job should plan, in order."""
    params = job.get("params") or {}
    requested = params.get("segments")
    if requested:
        all_segments = _arc_segments(ctx, run_name)
        by_id = {s.get("id"): s for s in all_segments if isinstance(s, dict)}
        selected = []
        for seg_id in requested:
            if seg_id in by_id:
                selected.append(by_id[seg_id])
            else:
                selected.append({"id": seg_id})
        return selected

    if params.get("rebrief"):
        selected = []
        for seg in _arc_segments(ctx, run_name):
            seg_id = seg.get("id")
            brief_path = artifact_path(ctx, run_name, "brief", seg_id)
            if brief_path.exists():
                with open(brief_path, "r", encoding="utf-8") as f:
                    brief = yaml.safe_load(f)
                if isinstance(brief, dict) and brief.get("needs_rebrief"):
                    selected.append(seg)
        return selected

    # Default: every segment in the arc plan that has no brief.yaml yet.
    selected = []
    for seg in _arc_segments(ctx, run_name):
        seg_id = seg.get("id")
        brief_path = artifact_path(ctx, run_name, "brief", seg_id)
        if not brief_path.exists():
            selected.append(seg)
    return selected


def _run_arc(ctx, job, run_name, pack, llm, vocab, progress, cancel_check,
             result) -> None:
    """Drive the arc stage and fold its output into `result`."""
    out_path = artifact_path(ctx, run_name, "arc_plan", "")
    on_llm_progress = _make_llm_progress(ctx, job["id"])
    try:
        ctx.plan_arc(pack, ctx.config, llm, vocab, out_path,
                     on_llm_progress=on_llm_progress)
    finally:
        # Clear the last in-flight snapshot so a finished/failed job never
        # shows a stale "still decoding" panel — see update_llm_progress's
        # docstring on why None means "nothing in flight right now". This
        # is itself best-effort: a store failure here must never mask
        # whatever plan_arc raised (or overshadow its success).
        try:
            ctx.store.update_llm_progress(job["id"], None)
        except Exception:
            log.warning("_run_arc: failed to clear llm_progress for job_id=%s",
                        job["id"], exc_info=True)

    if not out_path.exists():
        raise EmptyOutputError(
            f"arc stage wrote no arc plan at {out_path}")
    with open(out_path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    segments = doc.get("segments") if isinstance(doc, dict) else None
    if not isinstance(segments, list) or not segments:
        # Checked BEFORE mirroring so an empty plan never reaches the store,
        # where boot() would rehydrate it over a later, real run.
        raise EmptyOutputError(
            "arc stage produced an empty arc plan: every batch was skipped "
            f"(see the plan_arc warnings for this job); {out_path}")

    if _mirror(ctx, run_name, "arc_plan", "", job["id"]):
        result["artifacts"].append("arc_plan:")
    result["segments"] = len(segments)

    expected = _expected_segment_count(ctx.config)
    if expected is not None and len(segments) < expected:
        # A partial plan is still usable, so this completes — but the
        # shortfall goes on the job row rather than being silent.
        result["skipped_segments"] = expected - len(segments)
        log.warning("arc stage: planned %d of %d segments, %d batches skipped",
                    len(segments), expected, expected - len(segments))


def _run_segment(ctx, job, run_name, pack, llm, vocab, progress, cancel_check,
                 result) -> None:
    """Drive the segment stage and fold its output into `result`."""
    selected = _select_segments(ctx, run_name, job)
    planned = 0
    for arc_segment in selected:
        if cancel_check is not None and cancel_check():
            log.info("segment stage: cancel requested, stopping")
            break
        seg_id = arc_segment.get("id")
        out_path = artifact_path(ctx, run_name, "brief", seg_id)
        ctx.plan_segment(pack, arc_segment, ctx.config, llm, vocab, out_path,
                         progress=progress, cancel_check=cancel_check)
        if _mirror(ctx, run_name, "brief", seg_id, job["id"]):
            result["artifacts"].append(f"brief:{seg_id}")
        if _mirror(ctx, run_name, "tree", seg_id, job["id"]):
            result["artifacts"].append(f"tree:{seg_id}")
        planned += 1
    result["segments"] = planned


def _run_dialogue(ctx, job, run_name, pack, llm, vocab, progress, cancel_check,
                  result) -> None:
    """Drive the dialogue stage and fold its output into `result`."""
    params = job.get("params") or {}
    segment_ids = params.get("segments") or []
    stats = ctx.generate_dialogue(pack, segment_ids, ctx.config, llm,
                                  pathlib.Path(ctx.output_root) / run_name,
                                  progress=progress, cancel_check=cancel_check)
    if isinstance(stats, dict):
        for key, value in stats.items():
            if key not in ("artifacts", "segments", "duration_s"):
                result[key] = value
    for seg_id in segment_ids:
        if _mirror(ctx, run_name, "dialogue", seg_id, job["id"]):
            result["artifacts"].append(f"dialogue:{seg_id}")
        take_count = _mirror_dialogue_takes(ctx, run_name, seg_id, job["id"])
        if take_count:
            result["artifacts"].append(f"dialogue_takes:{seg_id}:{take_count}")


def _run_stage(ctx, job, run_name, pack, llm, vocab, progress, cancel_check,
               result) -> None:
    """Dispatch a single stage, or the full arc->segment->dialogue chain."""
    stage = job["stage"]
    if stage == "arc":
        _run_arc(ctx, job, run_name, pack, llm, vocab, progress, cancel_check,
                 result)
    elif stage == "segment":
        _run_segment(ctx, job, run_name, pack, llm, vocab, progress,
                     cancel_check, result)
    elif stage == "dialogue":
        _run_dialogue(ctx, job, run_name, pack, llm, vocab, progress,
                      cancel_check, result)
    elif stage == "all":
        _run_arc(ctx, job, run_name, pack, llm, vocab, progress, cancel_check,
                 result)
        if cancel_check is not None and cancel_check():
            log.info("all stage: cancel requested after arc")
            return
        _run_segment(ctx, job, run_name, pack, llm, vocab, progress,
                     cancel_check, result)
        if cancel_check is not None and cancel_check():
            log.info("all stage: cancel requested after segment")
            return
        _run_dialogue(ctx, job, run_name, pack, llm, vocab, progress,
                      cancel_check, result)
    else:
        raise ValueError(f"unknown stage {stage!r}")


def boot(ctx) -> None:
    """Run once at service start, before the dispatch loop.

    1. Reconcile orphaned running jobs (a container that restarted mid-job
       leaves a row running that nothing will ever clear).
    2. Rehydrate artifacts that are in the store but missing from the working
       directory. Never overwrite a file that already exists — the local copy
       is what the layer functions are mid-way through.

    Queued rows are left alone: they are work the operator submitted that has
    simply not started, and failing them on every restart would silently
    discard the queue.
    """
    log.debug("boot: reconciling orphans")
    ctx.store.reconcile_orphans()

    # Runs, not source packs — a run is the output namespace artifacts are
    # actually keyed on. `_pack_names`-style directory scanning under
    # pack_root would list source packs (which may have no output at all,
    # or whose latest run has a different, timestamped name).
    for run_name in ctx.store.list_runs():
        for artifact in ctx.store.list_artifacts(run_name):
            kind = artifact.get("kind")
            segment_id = artifact.get("segment_id", "")
            path = artifact_path(ctx, run_name, kind, segment_id)
            if path.exists():
                log.debug("boot: %s already on disk, not overwriting", path)
                continue
            content = ctx.store.load_artifact(run_name, kind, segment_id)
            if content is None:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(content, f, sort_keys=False, allow_unicode=True)
            log.info("boot: rehydrated %s", path)


def dispatch_once(ctx) -> bool:
    """Claim at most one job and run it to a terminal status.

    Returns True when a job was claimed (whatever its outcome), False when the
    queue was empty. Never lets an exception escape — one bad job must not
    wedge the queue for every job behind it.
    """
    rows = ctx.store.list_jobs(status="queued")
    if not rows:
        log.debug("dispatch_once: no queued jobs")
        return False

    # The listing is newest first, so the last row is the oldest queued job.
    job = rows[-1]
    job_id = job["id"]
    log.info("dispatch_once: claiming job_id=%s pack=%s stage=%s",
             job_id, job["pack"], job["stage"])

    if not ctx.store.mark_running(job_id):
        log.debug("dispatch_once: job_id=%s not claimed (lost the race)", job_id)
        return True

    pack_name = job["pack"]
    # Historical rows (submitted before `run` existed) have run=None; fall
    # back to pack_name so they keep writing where they always did.
    run_name = job.get("run") or pack_name
    result = {"artifacts": []}
    started = datetime.datetime.now(datetime.timezone.utc)

    try:
        pack = ctx.load_pack(ctx.pack_root / pack_name)
        profile = job.get("profile") or None
        llm = ctx.build_llm(profile, job["stage"])
        vocab = ctx.build_vocab(ctx.config, pack)
        progress = _make_progress(ctx, job_id)
        cancel_check = _make_cancel_check(ctx, job_id)
        _run_stage(ctx, job, run_name, pack, llm, vocab, progress, cancel_check,
                   result)
    except Exception as exc:
        log.exception("dispatch_once: job_id=%s failed", job_id)
        ctx.store.finish(job_id, "failed", error=str(exc))
        return True

    finished = datetime.datetime.now(datetime.timezone.utc)
    result["duration_s"] = (finished - started).total_seconds()

    if ctx.store.is_cancelled(job_id):
        log.info("dispatch_once: job_id=%s cancelled", job_id)
        ctx.store.finish(job_id, "cancelled", result=result)
    else:
        log.info("dispatch_once: job_id=%s completed", job_id)
        ctx.store.finish(job_id, "completed", result=result)
    return True


def run_forever(ctx, stop_event, poll_seconds: float = 1.0) -> None:
    """Loop over `dispatch_once` until `stop_event` is set.

    Uses `stop_event.wait` rather than `time.sleep` so shutdown is immediate.
    """
    log.debug("run_forever: starting dispatch loop")
    while not stop_event.is_set():
        if dispatch_once(ctx):
            continue
        stop_event.wait(poll_seconds)
    log.debug("run_forever: stopped")
