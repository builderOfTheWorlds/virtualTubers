"""
generator_api.py
HTTP surface for the 3-layer generator service.

This is the interface the management GUI drives. It does no generation
itself: it validates a request, writes a job row, and gets out of the way.
The runner does the work; these endpoints only ever read and write job
state.

Validation is the whole point of the module. A job that is going to fail
because the profile does not exist, or because `test_mode` was sent without
a segment, must be rejected at submit time with a 4xx the caller can act on
— not accepted, queued, and failed forty minutes later when a dispatcher
finally picks it up and discovers the same thing.

The module is named `generator_api` (not `api`) because
`services/message-api/api.py` already owns the bare name `api` on
sys.path; whichever imports first wins and the collision would make this
module's tests bind to the wrong module entirely.
"""
import logging
import math
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import JSONResponse

import generation_store
import runner

# Configure the root logger once, at import time — before anything else in
# this process calls logging.getLogger(...).info/.debug. Without this, every
# log.info/log.debug call across this service and the 3-layer generator
# library (generation_store, runner, plan_arc, concurrent_llm, ...) is
# silently discarded: Python's root logger defaults to WARNING with no
# handler attached, so nothing ever reaches `docker logs` even though the
# calls are already in the code. Uvicorn's own access/error logs configure
# their own loggers, which is why those show up while the app's never did.
# GENERATOR_LOG_LEVEL lets an operator raise verbosity (e.g. DEBUG) without
# a rebuild; defaults to INFO so job lifecycle and streaming-LLM text logs
# (concurrent_llm.complete_streaming) are visible by default.
logging.basicConfig(
    level=os.environ.get("GENERATOR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state — monkeypatchable by the test suite.
#
# Every endpoint reads these through the MODULE at call time (e.g.
# `store.get(...)`, `CONFIG[...]`), never through a name captured into a
# default argument or a closure at import time. The tests replace them by
# assigning to the module attribute, so a name imported at module load
# cannot be monkeypatched.
# ---------------------------------------------------------------------------
store = generation_store
CONFIG = None
PACK_ROOT = Path(os.environ.get("PACK_ROOT", "/data/packs"))
OUTPUT_ROOT = Path(os.environ.get("OUTPUT_DIR", "/data/output"))
DEFAULT_PACK = os.environ.get("DEFAULT_PACK", "ashiorid")
build_llm = None

# Runner handles, owned by the lifespan.
_runner_ctx = None
_runner_thread = None
_stop_event = threading.Event()

VALID_STAGES = {"arc", "segment", "dialogue", "all", "publish"}


# ---------------------------------------------------------------------------
# Config overlays
# ---------------------------------------------------------------------------

def overlay_output_dir(config, output_dir) -> dict:
    """Return the config with `config["output"]["dir"]` replaced by
    `output_dir`.

    `generation.yaml` ships `output.dir` as a REPO-RELATIVE HOST PATH, and
    the config directory is mounted read-only in the container, so it cannot
    be corrected in place. Left alone, `config.output_root()` resolves it
    against the container's working directory and every artifact lands
    inside the image layer instead of on the bind mount.
    """
    if output_dir is None or output_dir == "":
        return config
    config.setdefault("output", {})["dir"] = output_dir
    return config


def overlay_base_url(config, base_url) -> dict:
    """Return the config with `config["defaults"]["base_url"]` replaced by
    `base_url`.

    When `base_url` is None or empty, return the config UNCHANGED — a
    missing env var must not blank the configured default.

    The shipped `generation.yaml` carries
    `defaults.base_url: http://localhost:11434`, which inside a container
    means the CONTAINER, not the host running Ollama. Without this overlay
    the compose variable `OLLAMA_BASE_URL` has no consumer at all and every
    LLM call dies with ECONNREFUSED — while the job still finishes
    `completed`, because `plan_arc`'s contract is to log and skip a batch it
    cannot plan.
    """
    if base_url is None or base_url == "":
        return config
    config.setdefault("defaults", {})["base_url"] = base_url
    return config


# ---------------------------------------------------------------------------
# Pack path resolution
# ---------------------------------------------------------------------------

def resolve_pack_path(pack) -> Path:
    """Resolve a pack name to a directory under PACK_ROOT.

    Reject and raise `HTTPException(400, ...)` when `pack`:
      - contains "/" or "\\" or is absolute
      - is "." or ".." or contains ".."
      - does not exist as a directory under PACK_ROOT
    Otherwise return `PACK_ROOT / pack`.

    This is the ONE PLACE UNTRUSTED INPUT BECOMES A FILESYSTEM PATH. A name
    containing a separator or `..` must be rejected outright, not
    sanitised — there is no legitimate request that needs one.
    """
    if not isinstance(pack, str) or pack == "":
        raise HTTPException(status_code=400, detail=f"invalid pack name {pack!r}")
    if "/" in pack or "\\" in pack:
        raise HTTPException(
            status_code=400,
            detail=f"pack name {pack!r} must not contain a path separator",
        )
    if pack.startswith("/"):
        raise HTTPException(
            status_code=400,
            detail=f"pack name {pack!r} must not be absolute",
        )
    if pack in (".", "..") or ".." in pack:
        raise HTTPException(
            status_code=400,
            detail=f"pack name {pack!r} must not be '.' or '..'",
        )
    candidate = PACK_ROOT / pack
    if not candidate.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"pack {pack!r} does not exist under {PACK_ROOT}",
        )
    return candidate


def validate_run_name(run) -> str:
    """Reject a `run` value that isn't a safe single path component.

    Same separator/`..`/absolute checks as `resolve_pack_path`, but a `run`
    is an OUTPUT namespace under OUTPUT_ROOT, not a source pack — it need
    not (and for a freshly auto-generated run, will not) already exist on
    disk, so there is no directory-existence check here.
    """
    if not isinstance(run, str) or run == "":
        raise HTTPException(status_code=400, detail=f"invalid run name {run!r}")
    if "/" in run or "\\" in run:
        raise HTTPException(
            status_code=400,
            detail=f"run name {run!r} must not contain a path separator",
        )
    if run.startswith("/"):
        raise HTTPException(
            status_code=400,
            detail=f"run name {run!r} must not be absolute",
        )
    if run in (".", "..") or ".." in run:
        raise HTTPException(
            status_code=400,
            detail=f"run name {run!r} must not be '.' or '..'",
        )
    return run


def generate_run_name(pack: str) -> str:
    """A fresh output namespace for a top-of-pipeline run: `<pack>_<UTC
    timestamp>_<suffix>`, e.g. `ashiorid_20260823_193045_a1b2`. The random
    suffix guards against two submissions landing in the same wall-clock
    second — which second-resolution alone can't rule out even for jobs
    submitted one at a time by a human or a script, let alone two clients
    racing each other."""
    import datetime
    import uuid
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    return f"{pack}_{stamp}_{suffix}"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown for the FastAPI app.

    On startup:
      - loads generation.yaml from `os.environ["GENERATOR_CONFIG"]`, applies
        `overlay_output_dir(..., os.environ["OUTPUT_DIR"])`, assigns CONFIG
      - `store.ensure_schema()` — BEST EFFORT: wrap in try/except, log the
        error and continue. A dead database must not stop the service from
        booting.
      - `runner.boot(ctx)` then start `runner.run_forever(ctx, stop_event)`
        on a daemon thread
    On shutdown: sets the stop event and joins the thread with a timeout.

    The whole startup body is guarded in try/except so an import-time
    failure of a not-yet-built layer module cannot prevent the app object
    existing — the tests import the module with no environment set at all
    and must not blow up.
    """
    global CONFIG, _runner_ctx, _runner_thread, _stop_event

    try:
        config_path = os.environ.get("GENERATOR_CONFIG")
        if config_path:
            import config as config_module
            loaded = config_module.load_config(config_path)
            loaded = overlay_output_dir(
                loaded, os.environ.get("OUTPUT_DIR", "/data/output"))
            loaded = overlay_base_url(
                loaded, os.environ.get("OLLAMA_BASE_URL"))
            CONFIG = loaded
            log.info("lifespan: loaded config from %s", config_path)
        else:
            log.warning("lifespan: GENERATOR_CONFIG not set, CONFIG stays None")

        # Best-effort schema creation. A dead database must not stop the
        # service from booting, exactly as message-api treats
        # episode_store.ensure_schema.
        try:
            store.ensure_schema()
        except Exception as exc:
            log.warning("lifespan: ensure_schema failed (continuing): %s", exc)

        # The DB-active saved config (set via POST /configs/{id}/activate)
        # is meant to be the single source of truth for what a job actually
        # generates against — the dashboard's Saved Configurations card and
        # every job submitted afterward assume it. Without this, a container
        # recreate (image rebuild, `docker compose up -d`, host reboot)
        # silently reverts to whatever GENERATOR_CONFIG_FILE says in .env,
        # which can be a completely different pack/model tier than an
        # operator believes is active — confirmed to cost real GPU-hours
        # more than once. Loading the file first (above) and only then
        # overlaying the DB-active row (if any) means: a fresh install with
        # no saved configs yet still boots off the file exactly as before,
        # and a DB that is down/unreachable at boot degrades to the same
        # file-based behavior rather than blocking startup.
        try:
            active_row = store.get_active_config_row()
        except Exception as exc:
            active_row = None
            log.warning("lifespan: could not read active saved config "
                        "(continuing with file-based CONFIG): %s", exc)
        if active_row is not None:
            try:
                _apply_config_yaml(active_row["config_yaml"])
                log.info("lifespan: applied DB-active saved config %r "
                         "(overriding GENERATOR_CONFIG file)", active_row["name"])
            except Exception as exc:
                log.warning("lifespan: DB-active saved config %r failed to "
                            "apply (keeping file-based CONFIG): %s",
                            active_row.get("name"), exc)

        # Build the runner context and start the dispatch loop on a daemon
        # thread. Guarded so a missing layer module cannot prevent the app
        # from existing.
        try:
            _runner_ctx = runner.build_default_context(
                CONFIG, PACK_ROOT, OUTPUT_ROOT)
            runner.boot(_runner_ctx)
            _stop_event = threading.Event()
            _runner_thread = threading.Thread(
                target=runner.run_forever,
                args=(_runner_ctx, _stop_event),
                daemon=True,
            )
            _runner_thread.start()
            log.info("lifespan: dispatch loop started")
        except Exception as exc:
            log.warning("lifespan: runner startup failed (continuing): %s", exc)
    except Exception as exc:
        log.warning("lifespan: startup failed (continuing): %s", exc)

    yield

    # Shutdown: set the stop event and join the thread with a timeout.
    try:
        if _stop_event is not None:
            _stop_event.set()
        if _runner_thread is not None:
            _runner_thread.join(timeout=5)
            log.info("lifespan: dispatch loop stopped")
    except Exception as exc:
        log.warning("lifespan: shutdown failed: %s", exc)


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    """Liveness probe. No database call — this must stay green while
    Postgres is down, or a DB blip restarts the container mid-run."""
    return {"status": "ok"}


@app.post("/jobs")
def submit_job(body: dict):
    """Validate a job request and write a job row.

    Body (all optional except `stage`):
      pack, run, stage, profile, segments, dry_run, test_mode, rebrief, stingers

    Read the body with a plain `dict` parameter and IGNORE every key outside
    that list. A caller must not be able to set `status`, `result` or
    `finished_at` by naming them.
    """
    stage = body.get("stage")
    if stage == "all":
        stage = "arc"  # For the runner, 'all' means starting the pipeline at the top
    if stage not in VALID_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"stage {stage!r} must be one of {sorted(VALID_STAGES)}",
        )

    if body.get("stingers"):
        raise HTTPException(
            status_code=400,
            detail="stingers require events.yaml, not yet built",
        )

    pack = body.get("pack") or DEFAULT_PACK
    resolve_pack_path(pack)

    # `run` is the OUTPUT namespace (arc_plan/briefs/dialogue land under
    # OUTPUT_ROOT/<run>), separate from `pack` (the source cast/scenes/lore).
    # An "arc" job (which is what "all" normalizes to above) starts a fresh
    # pipeline, so it gets a timestamped run auto-generated when the caller
    # doesn't name one. "segment"/"dialogue" jobs continue a run an earlier
    # arc job already produced — auto-generating a new one for them would
    # point them at a run that doesn't exist, so `run` is required.
    # (`publish` needs an existing run for the same reason — it builds its
    # episode from that run's finished artifacts; there is no "arc job" to
    # point at, so the message is deliberately stage-agnostic.)
    run = body.get("run") or None
    if run is not None:
        run = validate_run_name(run)
    elif stage == "arc":
        run = generate_run_name(pack)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"run is required for stage {stage!r}; pass the run this "
                    "work belongs to (the name a prior arc job created)",
        )

    profile = body.get("profile") or ""
    if profile:
        stages = ["arc", "segment", "dialogue"] if stage == "all" else [stage]
        import config as config_module
        try:
            config_module.validate_profile_for_stages(CONFIG, profile, stages)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"profile {profile!r} is not valid for stage {stage!r}: {exc}",
            )

    segments = body.get("segments") or []
    if body.get("test_mode"):
        if len(segments) != 1:
            raise HTTPException(
                status_code=400,
                detail=f"test_mode requires exactly one segment, got {len(segments)}",
            )

    job_id = store.submit({
        "pack": pack,
        "run": run,
        "stage": stage,
        "profile": profile,
        "params": {
            "segments": segments,
            "dry_run": body.get("dry_run", False),
            "test_mode": body.get("test_mode", False),
            "rebrief": body.get("rebrief", False),
            # publish-stage-only: episode naming (the runner's
            # _run_publish reads these from job["params"]; ignored by every
            # other stage). Kept here (not in a separate top-level param) so
            # they travel with the job row the runner already reads, and are
            # ignored — not leaked into — the stages that don't use them.
            "episode_name": body.get("episode_name") or None,
            "episode_source": body.get("episode_source"),
            "episode_project": body.get("episode_project"),
        },
    })
    return {"id": job_id, "run": run, "status": "queued"}


@app.get("/jobs")
def list_jobs(pack: str = None, run: str = None, stage: str = None, status: str = None):
    """List jobs, passing through only the parameters that were supplied.
    Metadata only; never scan generated output."""
    return store.list_jobs(pack=pack, run=run, stage=stage, status=status)


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    """The full job record, or 404 when `store.get` returns None."""
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    return record


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Request cancellation of a job.

    - `store.get` is None -> 404.
    - status already terminal -> 409.
    - Otherwise `store.request_cancel(job_id)`.
    - When the job's stage is "arc", return HTTP 202 with a note that the
      arc stage is not interruptible.
    - Every other stage returns 200 with `{"status": "cancel_requested"}`.
    """
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")

    if record.get("status") in generation_store.TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job {job_id!r} is already {record['status']}",
        )

    store.request_cancel(job_id)

    if record.get("stage") == "arc":
        return JSONResponse(
            status_code=202,
            content={
                "status": "cancel_requested",
                "note": "arc stage is not interruptible; cancellation will be honored before the next stage",
            },
        )
    return {"status": "cancel_requested"}


@app.post("/preview")
def preview(body: dict):
    """Preview the tree shape that would be planned. NO job row, NO model
    call.

    Body: `{pack?, run?, stage: "segment", profile?, target_slots?}`.

    `run` names the output namespace the segment stage would continue —
    same fallback as job submission: when omitted, falls back to `pack` so
    a caller previewing against a pre-migration run (no `run` on its job
    row, output still under OUTPUT_ROOT/<pack>) keeps working.
    """
    pack = body.get("pack") or DEFAULT_PACK
    resolve_pack_path(pack)
    run = body.get("run") or pack

    # Root target_slots: read `<OUTPUT_ROOT>/<run>/arc_plan.yaml`.
    arc_plan_path = OUTPUT_ROOT / run / "arc_plan.yaml"
    require_arc_plan = CONFIG.get("preview", {}).get("require_arc_plan", True)

    if arc_plan_path.exists():
        # When the arc plan IS present, use CONFIG["segment"]["target_slots"].
        target_slots = CONFIG["segment"]["target_slots"]
    elif require_arc_plan:
        raise HTTPException(
            status_code=409,
            detail="arc plan not found; run the arc stage first",
        )
    else:
        # When the knob is false, take target_slots from the body.
        target_slots = body.get("target_slots")
        if target_slots is None:
            raise HTTPException(
                status_code=400,
                detail="target_slots must be supplied when require_arc_plan is false",
            )

    tree = CONFIG["segment"]["tree"]
    max_leaf_slots = tree["max_leaf_slots"]
    max_children = tree["max_children"]
    max_depth = tree["max_depth"]

    expected_children = min(math.ceil(target_slots / max_leaf_slots), max_children)
    expected_max_depth = max_depth

    # Per-level word budget derived from CONFIG["segment"]["target_words"].
    target_words = CONFIG["segment"]["target_words"]
    word_budget_per_level = target_words // (max_depth + 1)

    return {
        "target_slots": target_slots,
        "expected_children": expected_children,
        "expected_max_depth": expected_max_depth,
        "word_budget_per_level": word_budget_per_level,
    }


@app.get("/packs")
def list_packs():
    """Every pack name, now sourced from the pack_campaigns table (decision 5:
    Postgres is the source of truth for pack content) instead of scanning
    PACK_ROOT under this container. Response shape UNCHANGED — a plain sorted
    list — because the campaign-manager Pack dropdown and two existing tests
    (and the plan itself, despite one wrong test snippet) all depend on it.
    Packs appear here once a pack_campaigns row exists for them (see
    scripts/import_packs_to_postgres.py). No available() gate, matching
    /configs and /jobs: this container always has Postgres configured
    (depends_on generator-postgres), and when it is unreachable a 500 is the
    honest signal — campaign-manager already degrades its dropdown on any
    non-2xx."""
    return store.list_pack_names()


# ---------------------------------------------------------------------------
# Pack Viewer / Pack Editor — campaign pack content lives in Postgres now
# (pack_* tables, 2026-09 unified manager GUI plan). These endpoints are the
# ONLY way pack content is read or written: no container in this stack ever
# mounts campaigns/:rw, and campaign-manager reaches this content purely as
# an HTTP client of these endpoints (it holds no DB credentials of its own).
#
# Read endpoints (Phase 1): materialize the pack's rows into a container-local
# temp dir and hand it to the real campaign.pack.load_pack() — the same
# function the generation pipeline uses — so the browser sees exactly what a
# job would consume, nothing approximated.
#
# Write endpoints (Phase 2, Task 2.1): validate-before-insert. Every write is
# run through _validate_candidate_pack — materialize the CURRENT rows, apply
# the one proposed change, run the real load_pack() against the mutated copy —
# BEFORE anything reaches Postgres. A bad edit is a 422 and the store is left
# byte-for-byte untouched.
# ---------------------------------------------------------------------------


@app.get("/packs/{pack_name}")
def get_pack_summary(pack_name: str):
    """Full pack summary for the viewer: campaign metadata plus every scene
    and cast member's headline fields, and the lore-note names. Returns 404
    for an unknown pack and 422 for a pack whose stored rows do not load —
    the latter is actionable (it names the exact load_pack error), not a 500.

    Materializes to a container-local temp dir and always cleans it up,
    success or failure (materialize_pack's own contract).
    """
    import shutil
    from campaign.pack import load_pack, PackError

    try:
        root = store.materialize_pack(pack_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"pack {pack_name!r} not found")
    try:
        try:
            pack = load_pack(root)
        except PackError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"pack {pack_name!r} is stored but does not load: {exc}")
        # worker_id is a store concern, not a load_pack field — join it onto
        # the cast projection so the viewer's cast table can show/edit it.
        worker_by_member = {
            row["member_id"]: row.get("worker_id")
            for row in store.list_cast(pack_name)
        }
        return {
            "name": pack.name,
            "title": pack.title,
            "genre": pack.genre,
            "start_scene": pack.start_scene,
            "gm_id": pack.gm_id,
            "player_ids": pack.player_ids,
            "scenes": [
                {"id": s.id, "title": s.title, "ambient": s.ambient,
                 "default_next": s.default_next}
                for s in sorted(pack.scenes.values(), key=lambda s: s.id)
            ],
            "cast": [
                {"id": c.id, "name": c.name, "role": c.role,
                 "archetype": c.archetype,
                 "worker_id": worker_by_member.get(c.id)}
                for c in sorted(pack.cast.values(), key=lambda c: c.id)
            ],
            "lore_names": sorted(pack.lore.keys()),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


@app.get("/packs/{pack_name}/scenes/{scene_id}")
def get_pack_scene(pack_name: str, scene_id: str):
    """One scene: its raw stored YAML (what an edit form pre-fills) plus a
    404 when the scene does not exist in the pack."""
    for row in store.list_scenes(pack_name):
        if row["scene_id"] == scene_id:
            return {"scene_id": scene_id, "scene_yaml": row["scene_yaml"]}
    raise HTTPException(status_code=404,
                        detail=f"scene {scene_id!r} not found in pack {pack_name!r}")


@app.get("/packs/{pack_name}/cast/{member_id}")
def get_pack_cast_member(pack_name: str, member_id: str):
    """One cast member's raw YAML, with their worker_id (may be null — the
    'falls through to GM narration' state) exposed so the Pack Editor can
    show and edit it inline."""
    for row in store.list_cast(pack_name):
        if row["member_id"] == member_id:
            return {"member_id": member_id,
                    "member_yaml": row["member_yaml"],
                    "worker_id": row.get("worker_id")}
    raise HTTPException(status_code=404,
                        detail=f"cast member {member_id!r} not found in pack {pack_name!r}")


@app.get("/packs/{pack_name}/lore/{lore_name}")
def get_pack_lore(pack_name: str, lore_name: str):
    """One lore note's raw text."""
    for row in store.list_lore(pack_name):
        if row["lore_name"] == lore_name:
            return {"lore_name": lore_name, "lore_text": row["lore_text"]}
    raise HTTPException(status_code=404,
                        detail=f"lore note {lore_name!r} not found in pack {pack_name!r}")


def _validate_candidate_pack(pack_name: str, mutate) -> None:
    """The shared pre-commit gate every pack write endpoint calls through
    (decision 4: validate BEFORE the write ever reaches Postgres).

    1. Materialize pack_name's CURRENT rows (before this edit) into a
       container-local temp dir.
    2. Apply mutate(root) — exactly the one filesystem change this edit
       represents (write/overwrite one scene file, unlink one, etc.).
    3. Run the real load_pack() against the MUTATED copy.
    4. Success: return, and only then does the caller touch Postgres.
       Failure: 422 with load_pack's own message; the store is left
       byte-for-byte as it was (nothing was ever written to it).

    Always cleans up the temp dir, success or failure.
    """
    import shutil
    from campaign.pack import load_pack, PackError

    try:
        root = store.materialize_pack(pack_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404,
                            detail=f"pack {pack_name!r} not found")
    try:
        mutate(root)
        load_pack(root)
    except PackError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"this edit would make pack {pack_name!r} invalid, "
                   f"nothing was saved: {exc}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


@app.put("/packs/{pack_name}/scenes/{scene_id}")
def put_pack_scene(pack_name: str, scene_id: str, body: dict = Body(...)):
    """Create or replace one scene. Validation is two-layered (both
    load_pack-level, since a scene's YAML is the unit load_pack parses):

      1. `scene_yaml` must be valid YAML and its `id:` must equal
         `scene_id` — a mismatch would create/replace a DIFFERENT scene than
         the route claims, which is a caller bug, so it's a 400.
      2. The whole pack, WITH this scene mutated in place, must still pass
         the real load_pack() (decision 4's gate, _validate_candidate_pack)
         — this is what rejects e.g. dropping a required beat or breaking a
         branch, returning a 422 with load_pack's own diagnostic instead of
         letting a broken pack land in Postgres.

    Only after both pass does the row ever reach the store.
    """
    import yaml
    scene_yaml = body.get("scene_yaml")
    if not isinstance(scene_yaml, str) or not scene_yaml.strip():
        raise HTTPException(status_code=400, detail="scene_yaml is required")
    try:
        parsed = yaml.safe_load(scene_yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"scene_yaml is not valid YAML: {exc}")
    if not isinstance(parsed, dict) or parsed.get("id") != scene_id:
        raise HTTPException(status_code=400,
                            detail=f"scene_yaml must have id: {scene_id!r} to match the URL")

    def mutate(root):
        (root / "scenes" / f"{scene_id}.yaml").write_text(scene_yaml, encoding="utf-8")

    _validate_candidate_pack(pack_name, mutate)   # 422 before ANY DB write
    store.upsert_scene(pack_name, scene_id, scene_yaml)   # only reached if valid
    return {"status": "saved", "scene_id": scene_id}


@app.delete("/packs/{pack_name}/scenes/{scene_id}")
def delete_pack_scene(pack_name: str, scene_id: str):
    """Delete one scene. The decision-4 gate (a real load_pack over the
    pack-with-that-scene-removed) is what rejects a delete that would break
    the pack — most commonly the start scene, or any scene another scene's
    default_next/branches point at — with a 422, so Postgres never holds a
    pack load_pack would reject."""
    def mutate(root):
        path = root / "scenes" / f"{scene_id}.yaml"
        if not path.exists():
            raise HTTPException(status_code=404,
                                detail=f"scene {scene_id!r} not found in pack {pack_name!r}")
        path.unlink()

    _validate_candidate_pack(pack_name, mutate)   # 404/422 before ANY DB write
    store.delete_scene(pack_name, scene_id)       # only reached if the pack still loads
    return {"status": "deleted", "scene_id": scene_id}


@app.put("/packs/{pack_name}/cast/{member_id}")
def put_pack_cast_member(pack_name: str, member_id: str, body: dict = Body(...)):
    """Create or replace one cast member. `member_yaml` must be valid YAML
    with a `name:` (load_pack's minimum for a cast file). `worker_id` is
    optional and may be null — a null mapping is the 'falls through to GM
    narration' state, not an error. generation_store.upsert_cast_member
    keeps any previously-stored worker_id unless a new one is explicitly
    passed (COALESCE in the upsert), so saving just the YAML never wipes an
    operator's mapping."""
    import yaml
    member_yaml = body.get("member_yaml")
    if not isinstance(member_yaml, str) or not member_yaml.strip():
        raise HTTPException(status_code=400, detail="member_yaml is required")
    try:
        parsed = yaml.safe_load(member_yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"member_yaml is not valid YAML: {exc}")
    if not isinstance(parsed, dict) or not parsed.get("name"):
        raise HTTPException(status_code=400, detail="member_yaml must have a name: field")

    worker_id = body.get("worker_id")
    if worker_id is not None and not isinstance(worker_id, str):
        raise HTTPException(status_code=400, detail="worker_id must be a string or null")

    def mutate(root):
        (root / "cast" / f"{member_id}.yaml").write_text(member_yaml, encoding="utf-8")

    _validate_candidate_pack(pack_name, mutate)   # 404/422 before ANY DB write
    store.upsert_cast_member(pack_name, member_id, member_yaml,
                             worker_id if isinstance(worker_id, str) and worker_id else None)
    return {"status": "saved", "member_id": member_id, "worker_id": worker_id or None}


@app.delete("/packs/{pack_name}/cast/{member_id}")
def delete_pack_cast_member(pack_name: str, member_id: str):
    def mutate(root):
        path = root / "cast" / f"{member_id}.yaml"
        if not path.exists():
            raise HTTPException(status_code=404,
                                detail=f"cast member {member_id!r} not found in pack {pack_name!r}")
        path.unlink()

    _validate_candidate_pack(pack_name, mutate)
    store.delete_cast_member(pack_name, member_id)
    return {"status": "deleted", "member_id": member_id}


@app.put("/packs/{pack_name}/lore/{lore_name}")
def put_pack_lore(pack_name: str, lore_name: str, body: dict = Body(...)):
    """Create or replace one lore note (arbitrary text — load_pack stores it
    verbatim, so the decision-4 gate here mostly guards against an empty
    pack's first-write path rather than shape validation)."""
    lore_text = body.get("lore_text")
    if not isinstance(lore_text, str):
        raise HTTPException(status_code=400, detail="lore_text is required")

    def mutate(root):
        lore_dir = root / "lore"
        lore_dir.mkdir(exist_ok=True)
        (lore_dir / f"{lore_name}.md").write_text(lore_text, encoding="utf-8")

    _validate_candidate_pack(pack_name, mutate)
    store.upsert_lore(pack_name, lore_name, lore_text)
    return {"status": "saved", "lore_name": lore_name}


@app.delete("/packs/{pack_name}/lore/{lore_name}")
def delete_pack_lore(pack_name: str, lore_name: str):
    def mutate(root):
        path = root / "lore" / f"{lore_name}.md"
        if not path.exists():
            raise HTTPException(status_code=404,
                                detail=f"lore note {lore_name!r} not found in pack {pack_name!r}")
        path.unlink()

    _validate_candidate_pack(pack_name, mutate)
    store.delete_lore(pack_name, lore_name)
    return {"status": "deleted", "lore_name": lore_name}


@app.get("/profiles")
def list_profiles():
    """Configured model profile names per layer, e.g.
    {"arc": ["light", "heavy"], "segment": [...], "dialogue": [...]}.
    Drives the GUI's Profile dropdown so an operator picks a profile that
    `validate_profile_for_stages` will actually accept, instead of typing a
    free-text name and finding out it's wrong only after a 400 at submit
    time. Returns empty lists for a layer that fails to resolve (e.g.
    CONFIG not loaded yet) rather than raising — this is a GUI convenience
    endpoint, not a validation gate."""
    import config as config_module
    result = {}
    for layer in ("arc", "segment", "dialogue"):
        try:
            result[layer] = config_module.profile_names(CONFIG or {}, layer)
        except Exception:
            result[layer] = []
    return result


@app.get("/config")
def get_config(pack: str = None):
    """The loaded CONFIG, plus a `resolved_profile` key mapping each of the
    three layers to `config.resolve_profile(CONFIG, layer)`. 400 on a pack
    that fails resolve_pack_path. For GUI display only."""
    if pack is not None:
        resolve_pack_path(pack)

    import config as config_module
    resolved_profile = {}
    for layer in ("arc", "segment", "dialogue"):
        try:
            resolved_profile[layer] = config_module.resolve_profile(CONFIG, layer)
        except Exception:
            resolved_profile[layer] = None

    result = dict(CONFIG)
    result["resolved_profile"] = resolved_profile
    return result


# ---------------------------------------------------------------------------
# Generated data viewer — read-only browse surface over generation_artifacts.
# Metadata listing stays cheap (no content column, per list_artifacts'
# own docstring); a single artifact's full document is only fetched when
# the GUI drills into it.
# ---------------------------------------------------------------------------

@app.get("/runs")
def list_runs():
    """Every distinct output namespace a job has ever written to, sorted.
    Drives the run picker in the data viewer."""
    return store.list_runs()


@app.get("/artifacts")
def list_artifacts_endpoint(run: str):
    """Metadata for every artifact under one run (arc plan, briefs, trees,
    dialogue) — no content, so listing hundreds of segments stays a small
    response. `run` is required: an unscoped listing across every run in
    the database is never what the viewer wants."""
    return store.list_artifacts(run)


@app.get("/artifacts/{artifact_id}")
def get_artifact_endpoint(artifact_id: int):
    """One artifact's full document by primary key, or 404."""
    row = store.get_artifact(artifact_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"artifact {artifact_id!r} not found")
    return row


@app.post("/publish/{run}/upload")
def upload_run_episode(run: str, name: str | None = None, overwrite: bool = False):
    """Phase 3: upload a `publish` job's episode.json into the episode store.

    The publish stage (runner._run_publish) already wrote
    `${OUTPUT_ROOT}/${run}/episode.json`. This endpoint doesn't re-derive
    or re-validate that file — message-api's own POST /replays is the single
    place validation happens (same rule the whole plan applies: exactly
    one service owns a piece of validation, the rest of the stack forwards
    it). Here we're the forwarding side: read the file, relay it verbatim,
    relay message-api's status back (including its 4xx/409 with the real
    reason), and let campaign-manager's Publish flow surface that to the
    operator.

    `name` defaults to the episode's own `source` field (message-api's documented
    behavior), so the caller usually doesn't need to pass it — but passing it
    overrides, which is what the original `--name` CLI flag meant for
    `.claude/prompts/build_generated_episode.py`.
    """
    import httpx

    episode_path = OUTPUT_ROOT / run / "episode.json"
    if not episode_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no episode.json under {run!r} — was a `publish` job for "
                   f"this run run (and completed) yet?",
        )
    body = episode_path.read_bytes()

    message_api = os.environ.get("MESSAGE_API_URL", "http://127.0.0.1:8090")
    url = f"{message_api.rstrip('/')}/replays"
    query = {}
    if name:
        query["name"] = name
    if overwrite:
        query["overwrite"] = "true"
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                url, content=body, headers={"Content-Type": "application/json"},
                params=query)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"message-api ({message_api}) unreachable: {exc}",
        )

    try:
        detail = resp.json()
    except Exception:
        detail = resp.text[:500]
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail=detail)
    if isinstance(detail, dict):
        return {"episode": run, "status": "uploaded", **detail}
    return {"episode": run, "status": "uploaded", "message_api": detail}


# ---------------------------------------------------------------------------
# Saved configs — the management GUI's "Saved Configurations" section.
#
# Every mutating endpoint here VALIDATES the YAML by actually parsing it
# (config_module.load_config-equivalent) before writing anything, so a saved
# row can never contain unparseable YAML that only blows up later when
# someone activates it. Activation goes further: it also re-derives the
# resolved_profile for all three layers, the same check /config does, so a
# structurally-valid-but-wrong-shaped document (missing an `arc:` block,
# say) is rejected at activate time with a clear 400, not a 500 the next
# time a job tries to read CONFIG["arc"].
# ---------------------------------------------------------------------------

def _parse_config_yaml_or_400(config_yaml: str) -> dict:
    """Parse `config_yaml` the same way config.load_config parses a file on
    disk, without needing a real file. Raises HTTPException(400) on
    malformed YAML or a non-mapping top level."""
    import yaml
    try:
        parsed = yaml.safe_load(config_yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"malformed YAML: {exc}")
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="config must be a YAML mapping at the top level")
    return parsed


@app.get("/configs")
def list_saved_configs():
    """Every saved config, newest first, each carrying its own is_active
    flag — the GUI needs no separate 'what's active' call."""
    return store.list_configs()


@app.post("/configs")
def create_saved_config(body: dict):
    """Save a new named config. Body: {name, description?, config_yaml}.

    `config_yaml` is validated by parsing it, but NOT required to resolve
    every profile — a work-in-progress draft (e.g. missing the `dialogue:`
    block while an operator is still filling it in) can be saved and edited
    further before it's ever activated, where the stricter check applies.
    """
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    name = name.strip()
    description = body.get("description") or ""
    config_yaml = body.get("config_yaml")
    if not isinstance(config_yaml, str) or not config_yaml.strip():
        raise HTTPException(status_code=400, detail="config_yaml is required")

    _parse_config_yaml_or_400(config_yaml)

    try:
        config_id = store.create_config(name, description, config_yaml)
    except Exception as exc:
        # psycopg2's IntegrityError on the UNIQUE(name) constraint — surface
        # as 409, not a bare 500, so the GUI can say "name already taken".
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"a saved config named {name!r} already exists")
        raise HTTPException(status_code=500, detail=f"failed to save config: {exc}")

    return {"id": config_id, "name": name}


@app.get("/configs/{config_id}")
def get_saved_config(config_id: int):
    """One saved config by id, or 404."""
    row = store.get_config_row(config_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"config {config_id!r} not found")
    return row


@app.put("/configs/{config_id}")
def update_saved_config(config_id: int, body: dict):
    """Edit a saved config's description/config_yaml (name is immutable —
    see generation_store.update_config's docstring). Body: {description?,
    config_yaml}."""
    row = store.get_config_row(config_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"config {config_id!r} not found")

    config_yaml = body.get("config_yaml")
    if not isinstance(config_yaml, str) or not config_yaml.strip():
        raise HTTPException(status_code=400, detail="config_yaml is required")
    _parse_config_yaml_or_400(config_yaml)

    description = body.get("description")
    if description is None:
        description = row["description"]

    store.update_config(config_id, description, config_yaml)

    # If the edited row is the one currently live, re-apply it immediately
    # so an operator fixing a typo in the active config doesn't also have
    # to remember to re-activate it.
    if row["is_active"]:
        _apply_config_yaml(config_yaml)

    return store.get_config_row(config_id)


@app.delete("/configs/{config_id}")
def delete_saved_config(config_id: int):
    """Remove a saved config. Deleting the active one does NOT touch the
    live CONFIG — the dispatcher keeps running whatever was last applied,
    it just has no saved row to point back to anymore."""
    row = store.get_config_row(config_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"config {config_id!r} not found")
    store.delete_config(config_id)
    return {"status": "deleted", "id": config_id}


def _apply_config_yaml(config_yaml: str) -> None:
    """Parse `config_yaml`, apply the same overlays lifespan() applies to a
    file-loaded config, validate it resolves every layer's profile, then
    mutate the module-level CONFIG dict IN PLACE (clear + update, never
    reassign the name) so every existing reference — this module's CONFIG,
    runner.Context.config, and the build_llm closure over it — sees the
    change immediately with no service restart.

    Raises HTTPException(400) if the config doesn't resolve, so a broken
    saved row can never take down the running dispatcher.
    """
    import config as config_module

    parsed = _parse_config_yaml_or_400(config_yaml)
    parsed = overlay_output_dir(parsed, os.environ.get("OUTPUT_DIR", "/data/output"))
    parsed = overlay_base_url(parsed, os.environ.get("OLLAMA_BASE_URL"))

    for layer in ("arc", "segment", "dialogue"):
        try:
            config_module.resolve_profile(parsed, layer)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"config does not resolve a valid {layer!r} profile: {exc}",
            )

    global CONFIG
    if CONFIG is None:
        # Boot-time GENERATOR_CONFIG was never set (CONFIG stayed None per
        # lifespan()'s own comment) — nothing to mutate in place yet, so
        # start owning a real dict now. Every reference taken AFTER this
        # point (runner.Context.config, closures) will see it; anything
        # that captured the old `None` before activation can't have
        # meaningfully used it anyway.
        CONFIG = parsed
    else:
        CONFIG.clear()
        CONFIG.update(parsed)
    log.info("_apply_config_yaml: CONFIG updated in place")


@app.post("/configs/{config_id}/activate")
def activate_saved_config(config_id: int):
    """Make this saved config the live one: validates it, applies it to the
    running service in place (no restart), then marks it active in the
    store. Applying BEFORE marking active means a config that fails
    validation never gets marked active with a CONFIG that doesn't match —
    the store and the running process cannot drift out of sync on failure.
    """
    row = store.get_config_row(config_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"config {config_id!r} not found")

    _apply_config_yaml(row["config_yaml"])
    store.activate_config(config_id)

    return {"status": "activated", "id": config_id, "name": row["name"]}
