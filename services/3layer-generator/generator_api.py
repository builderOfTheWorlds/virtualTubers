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

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

import generation_store
import runner

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

VALID_STAGES = {"arc", "segment", "dialogue", "all"}


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
      pack, stage, profile, segments, dry_run, test_mode, rebrief, stingers

    Read the body with a plain `dict` parameter and IGNORE every key outside
    that list. A caller must not be able to set `status`, `result` or
    `finished_at` by naming them.
    """
    stage = body.get("stage")
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
        "stage": stage,
        "profile": profile,
        "params": {
            "segments": segments,
            "dry_run": body.get("dry_run", False),
            "test_mode": body.get("test_mode", False),
            "rebrief": body.get("rebrief", False),
        },
    })
    return {"id": job_id, "status": "queued"}


@app.get("/jobs")
def list_jobs(pack: str = None, stage: str = None, status: str = None):
    """List jobs, passing through only the parameters that were supplied.
    Metadata only; never scan generated output."""
    return store.list_jobs(pack=pack, stage=stage, status=status)


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

    Body: `{pack?, stage: "segment", profile?, target_slots?}`.
    """
    pack = body.get("pack") or DEFAULT_PACK
    resolve_pack_path(pack)

    # Root target_slots: read `<OUTPUT_ROOT>/<pack>/arc_plan.yaml`.
    arc_plan_path = OUTPUT_ROOT / pack / "arc_plan.yaml"
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
