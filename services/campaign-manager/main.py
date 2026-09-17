
import asyncio
import os
import json
import logging
import base64
import secrets
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx

import log_reader

# ---------------------------------------------------------------------------
# Configuration & Setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
# In production (Docker), this would be http://3layer-generator:8000
GENERATOR_API_URL = os.environ.get("GENERATOR_API_URL", "http://localhost:8000")
GENERATOR_CONTAINER_NAME = os.environ.get("GENERATOR_CONTAINER_NAME", "virtualtubers-3layer-generator-1")
# control-panel (the Rerun Theater viewer / log / worker manager) — the
# Publish flow cross-links INTO it (job_detail.html's "view this episode in
# the control panel" banner) rather than duplicating its viewer. Port 8091
# matches the docker-compose mapping already in place.
CONTROL_PANEL_URL = os.environ.get("CONTROL_PANEL_URL", "http://localhost:8091")

log = logging.getLogger("campaign-manager")

# ── Optional HTTP Basic Auth — no-ops unless both vars are set ──────────────
# Copied from services/control-panel/panel.py (the reference implementation
# this decision 1 asks us to match — same functions, same `secrets.
# compare_digest` timing-safe comparison, same /healthz bypass so the
# container healthcheck never needs credentials). Env-var names differ
# (CAMPAIGN_MANAGER_ vs CONTROL_PANEL_, this service's own convention);
# see control-panel's `CONTROL_PANEL_BASIC_AUTH_*`.
BASIC_AUTH_USER = os.environ.get("CAMPAIGN_MANAGER_BASIC_AUTH_USER", "")
BASIC_AUTH_PASS = os.environ.get("CAMPAIGN_MANAGER_BASIC_AUTH_PASS", "")


def _auth_enabled() -> bool:
    return bool(BASIC_AUTH_USER and BASIC_AUTH_PASS)


app = FastAPI()
# check_dir=False so importing `main` (tests, local dev, subprocess tooling)
# does not crash on a checkout where an empty `static/` dir isn't materialized
# yet — the Dockerfile still mkdirs it before COPY, and a mount that serves
# nothing must not take the whole app down. (No template references /static.)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static"),
                                 check_dir=False), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if not _auth_enabled() or request.url.path == "/healthz":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    user = pw = ""
    if header.startswith("Basic "):
        try:
            user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except Exception:
            log.warning("malformed Authorization header")
    if secrets.compare_digest(user, BASIC_AUTH_USER) and secrets.compare_digest(pw, BASIC_AUTH_PASS):
        return await call_next(request)
    return PlainTextResponse(
        "authentication required", status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="campaign-manager"'},
    )

http_client = httpx.AsyncClient(base_url=GENERATOR_API_URL, timeout=10.0)

# Constants for the template context
WORKER_IDS = ["coder", "manager", "tester"]
MESSAGE_TYPE_EXAMPLES = ["operator_message", "status_update"]


def _dashboard_context(request: Request, jobs=None, jobs_error=None, message_result=None,
                        configs=None, configs_error=None, packs=None, packs_error=None,
                        profiles=None, profiles_error=None, runs=None, runs_error=None,
                        form_error=None, run_filter=""):
    """Shared context builder for every route that renders dashboard.html,
    so every render passes the same full set of variables the template
    expects (avoids Jinja2 UndefinedError on partial contexts)."""
    return {
        "request": request,
        "jobs": jobs or [],
        "jobs_error": jobs_error,
        "generator_api_url": GENERATOR_API_URL,
        "control_panel_url": CONTROL_PANEL_URL,
        "worker_ids": WORKER_IDS,
        "log_types": [],
        "replays": [],
        "replays_error": None,
        "message_result": message_result,
        "prune_result": None,
        "message_type_examples": MESSAGE_TYPE_EXAMPLES,
        "configs": configs or [],
        "configs_error": configs_error,
        "packs": packs or [],
        "packs_error": packs_error,
        "profiles": profiles or {"arc": [], "segment": [], "dialogue": []},
        "profiles_error": profiles_error,
        "runs": runs or [],
        "runs_error": runs_error,
        "form_error": form_error,
        "run_filter": run_filter,
    }


async def _fetch_packs():
    """Best-effort fetch of every campaign pack for the Pack dropdown. Never
    raises — a generator-api hiccup degrades to an empty list with an error
    string, same pattern as _fetch_configs."""
    try:
        result = await http_client.get("/packs")
    except httpx.RequestError as exc:
        return [], f"generator-api unreachable: {exc}"
    if not result.is_success:
        return [], f"API error: {result.status_code}"
    try:
        packs = result.json()
        if not isinstance(packs, list):
            return [], "Failed to parse packs JSON"
        return packs, None
    except Exception:
        return [], "Failed to parse packs JSON"


async def _fetch_profiles():
    """Best-effort fetch of per-layer model profile names for the Profile
    dropdown. Never raises — degrades to empty lists with an error string,
    same pattern as _fetch_configs."""
    empty = {"arc": [], "segment": [], "dialogue": []}
    try:
        result = await http_client.get("/profiles")
    except httpx.RequestError as exc:
        return empty, f"generator-api unreachable: {exc}"
    if not result.is_success:
        return empty, f"API error: {result.status_code}"
    try:
        profiles = result.json()
        if not isinstance(profiles, dict):
            return empty, "Failed to parse profiles JSON"
        return profiles, None
    except Exception:
        return empty, "Failed to parse profiles JSON"


async def _fetch_configs():
    """Best-effort fetch of saved configs for the dashboard's Saved
    Configurations card. Never raises — a generator-api hiccup degrades to
    an empty list with an error string, same pattern as _fetch jobs."""
    try:
        result = await http_client.get("/configs")
    except httpx.RequestError as exc:
        return [], f"generator-api unreachable: {exc}"
    if not result.is_success:
        return [], f"API error: {result.status_code}"
    try:
        configs = result.json()
        if not isinstance(configs, list):
            return [], "Failed to parse configs JSON"
        return configs, None
    except Exception:
        return [], "Failed to parse configs JSON"


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, run: str = ""):
    """Main view: list all jobs and current generation status.

    `run` (optional query param) filters the job list down to just the jobs
    sharing that output namespace — how an operator sees which arc/segment/
    dialogue job ids belong to the same campaign generation, since the job
    table's own id has no relation to the others beyond that shared `run`.
    """
    jobs = []
    jobs_error = None
    jobs_params = {"run": run} if run else None
    try:
        jobs_result = await http_client.get("/jobs", params=jobs_params)
    except httpx.RequestError as exc:
        configs, configs_error = await _fetch_configs()
        packs, packs_error = await _fetch_packs()
        profiles, profiles_error = await _fetch_profiles()
        runs, runs_error = await _fetch_runs()
        return templates.TemplateResponse(
            request, "dashboard.html",
            _dashboard_context(request, jobs=[], jobs_error=f"generator-api unreachable: {exc}",
                                configs=configs, configs_error=configs_error,
                                packs=packs, packs_error=packs_error,
                                profiles=profiles, profiles_error=profiles_error,
                                runs=runs, runs_error=runs_error, run_filter=run)
        )

    if jobs_result.is_success:
        try:
            jobs = jobs_result.json()
            if not isinstance(jobs, list):
                jobs = []
        except Exception:
            jobs = []
            jobs_error = "Failed to parse jobs JSON"
    else:
        jobs = []
        jobs_error = f"API error: {jobs_result.status_code}"

    configs, configs_error = await _fetch_configs()
    packs, packs_error = await _fetch_packs()
    profiles, profiles_error = await _fetch_profiles()
    runs, runs_error = await _fetch_runs()
    return templates.TemplateResponse(
        request, "dashboard.html",
        _dashboard_context(request, jobs=jobs, jobs_error=jobs_error,
                            configs=configs, configs_error=configs_error,
                            packs=packs, packs_error=packs_error,
                            profiles=profiles, profiles_error=profiles_error,
                            runs=runs, runs_error=runs_error, run_filter=run)
    )


@app.get("/job/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str):
    """Detailed view for a single job: results, params, error logs, and
    live container log output for the window the job ran in."""
    job_result = await http_client.get(f"/jobs/{job_id}")
    if not job_result.is_success:
        return templates.TemplateResponse(
            request, "error.html", {"request": request, "error": job_result.text}
        )

    job = job_result.json()
    logs = log_reader.get_job_logs(
        GENERATOR_CONTAINER_NAME,
        started_at=job.get("started_at"),
        finished_at=job.get("finished_at"),
    )

    # Fetch artifacts for this run (pack)
    artifacts = []
    artifacts_error = None
    if job.get("run"):
        try:
            result = await http_client.get("/artifacts", params={"run": job["run"]})
            if result.is_success:
                artifacts = result.json()
            else:
                artifacts_error = f"API error: {result.status_code}"
        except Exception as exc:
            artifacts_error = f"generator-api unreachable: {exc}"

    return templates.TemplateResponse(
        request, "job_detail.html",
        {
            "request": request,
            "job": job,
            "logs": logs,
            "logs_available": log_reader.available(),
            "artifacts": artifacts,
            "artifacts_error": artifacts_error,
            # Phase 3 publish banner (set by /jobs/{id}/publish's redirect
            # query string — no server-side state to lose on the redirect).
            "published": request.query_params.get("published") == "1",
            "publish_episode_name": request.query_params.get("episode_name") or "",
            "publish_event_count": request.query_params.get("event_count") or "",
            "publish_error": request.query_params.get("publish_error") or "",
            "control_panel_url": request.query_params.get("control_panel_url")
                                 or CONTROL_PANEL_URL,
        }
    )


@app.get("/job/{job_id}/logs", response_class=HTMLResponse)
async def job_logs_partial(request: Request, job_id: str):
    """HTMX polling target: just the log panel, re-fetched on an interval
    while the job is still running. Keeps the rest of the page (params,
    status badge) from re-rendering on every poll tick."""
    job_result = await http_client.get(f"/jobs/{job_id}")
    if not job_result.is_success:
        return HTMLResponse(f"<div class='alert alert-danger'>{job_result.text}</div>")

    job = job_result.json()
    logs = log_reader.get_job_logs(
        GENERATOR_CONTAINER_NAME,
        started_at=job.get("started_at"),
        finished_at=job.get("finished_at"),
    )

    # Fetch artifacts for this run (pack)
    artifacts = []
    artifacts_error = None
    if job.get("run"):
        try:
            result = await http_client.get("/artifacts", params={"run": job["run"]})
            if result.is_success:
                artifacts = result.json()
            else:
                artifacts_error = f"API error: {result.status_code}"
        except Exception as exc:
            artifacts_error = f"generator-api unreachable: {exc}"

    return templates.TemplateResponse(
        request, "_job_logs.html",
        {
            "request": request,
            "job": job,
            "logs": logs,
            "logs_available": log_reader.available(),
            "artifacts": artifacts,
            "artifacts_error": artifacts_error
        }
    )


@app.post("/jobs/submit", response_class=HTMLResponse)
async def submit_job_ui(
    request: Request,
    pack: str = Form(...),
    stage: str = Form(...),
    profile: str = Form(""),
    run: str = Form(""),
    segments_json: str = Form("[]")
):
    """Handle form submission for a new generation job.

    `run` continues an existing arc's output namespace (required for
    segment/dialogue, per generator_api.submit_job) — left blank on an arc
    submission to start a fresh run. Re-renders the dashboard with the
    submitted values preserved and an inline error on any failure, rather
    than losing the form to a bare error page.
    """
    try:
        segments = json.loads(segments_json)
    except json.JSONDecodeError:
        return await _render_dashboard_with_error(request, "Invalid segments JSON")

    payload = {
        "pack": pack,
        "stage": stage,
        "profile": profile,
        "segments": segments,
        "dry_run": False,
        "test_mode": False,
        "rebrief": False,
    }
    if run.strip():
        payload["run"] = run.strip()

    result = await http_client.post("/jobs", json=payload)

    if result.is_success:
        return RedirectResponse(url="/", status_code=303)
    return await _render_dashboard_with_error(request, _extract_error_detail(result))


async def _render_dashboard_with_error(request: Request, error: str):
    """Re-render the full dashboard with `form_error` set, so a rejected
    submission (bad segments JSON, missing run, unknown profile, ...) shows
    the operator what went wrong instead of a bare error page that drops
    the job list and every other card."""
    jobs = []
    jobs_error = None
    try:
        jobs_result = await http_client.get("/jobs")
        if jobs_result.is_success:
            jobs = jobs_result.json()
            if not isinstance(jobs, list):
                jobs = []
    except httpx.RequestError as exc:
        jobs_error = f"generator-api unreachable: {exc}"

    configs, configs_error = await _fetch_configs()
    packs, packs_error = await _fetch_packs()
    profiles, profiles_error = await _fetch_profiles()
    runs, runs_error = await _fetch_runs()
    return templates.TemplateResponse(
        request, "dashboard.html",
        _dashboard_context(request, jobs=jobs, jobs_error=jobs_error,
                            configs=configs, configs_error=configs_error,
                            packs=packs, packs_error=packs_error,
                            profiles=profiles, profiles_error=profiles_error,
                            runs=runs, runs_error=runs_error,
                            form_error=error)
    )


@app.post("/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def cancel_job_ui(request: Request, job_id: str):
    """Trigger job cancellation."""
    result = await http_client.post(f"/jobs/{job_id}/cancel")

    if result.is_success:
        return HTMLResponse("<div class='alert alert-success'>Cancel requested</div>")
    else:
        return HTMLResponse(f"<div class='alert alert-danger'>{result.text}</div>")


# How long to wait for a `publish` job to finish (it does no LLM work — pure
# conversion of finished artifacts — so a generous bounded wait finishes it in
# practice; on timeout we redirect to the job's own page, which keeps polling
# and where the operator can also submit the upload manually via the banner.)
_PUBLISH_WAIT_BUDGET_S = 120
_PUBLISH_POLL_INTERVAL_S = 1.0

# A `publish` job for a completed dialogue job's run: the episode key is
# derived from the pack + run's 4-char uuid suffix — matching the original
# script's documented convention (`ashiorid_generated_ce8d`, not
# `<pack>_<timestamp>_<suffix>` which is the RUN namespace, not the
# episode library key) so an operator's muscle memory from the old CLI
# still fits.
def _publish_episode_name(pack: str, run: str) -> str:
    suffix = run.rsplit("_", 1)[-1] if run else ""
    return f"{pack}_generated_{suffix}" if suffix else f"{pack}_generated"


async def _wait_for_job_terminal(http, job_id: str, budget_s: float,
                                 interval_s: float):
    """Poll one job until it's no longer queued/running (or the budget runs
    out). Returns the job row, or None on budget exhaustion. Kept separate
    from the route so it can be exercised without touching the route's
    redirect-and-template rendering.
    """
    import time
    deadline = time.monotonic() + budget_s
    while True:
        r = await http.get(f"/jobs/{job_id}")
        if not r.is_success:
            return None
        job = r.json()
        if job.get("status") not in ("queued", "running"):
            return job
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(interval_s)


@app.post("/jobs/{job_id}/publish", response_class=HTMLResponse)
async def publish_job_ui(request: Request, job_id: str):
    """Phase 3 (task 3.2): turn a completed dialogue job's run into an
    episode in the library.

    Flow, entirely over the generator API (this service stays a pure HTTP
    client — no Postgres, no filesystem, same as every other route in this
    file):
      1. Read the job — it must exist, be completed, and have a `run`.
      2. Submit the `publish` job for that run (with a derived `episode_name`,
         per the original build_generated_episode.py's convention).
      3. Wait (bounded) for it to finish.
      4. Call the generator's `/publish/{run}/upload` (task 3.1's Step 4),
         which proxies the finished `episode.json` to message-api's own
         `POST /replays` (the one real validator, per its module's own rule).

    Every step's real error is relayed back to the job_detail page in a
    banner (query string — no session state to lose on redirect) rather
    than a bare 500, so an operator can read exactly what failed and
    retry from the same place.
    """
    # 1. The job this button was asked to publish from.
    job_result = await http_client.get(f"/jobs/{job_id}")
    if not job_result.is_success:
        return _publish_redirect(job_id,
                                 error=f"generator-api error: {job_result.status_code}")
    job = job_result.json()
    run = job.get("run")
    pack = job.get("pack")
    if job.get("status") != "completed":
        return _publish_redirect(job_id,
                                 error=f"job {job_id} is {job.get('status')!r} "
                                       f"(not completed), can't publish from it")
    if not run:
        return _publish_redirect(job_id,
                                 error=f"job {job_id} has no run namespace to publish")

    # 2. Submit the publish job for that run.
    episode_name = _publish_episode_name(pack, run)
    submit_result = await http_client.post("/jobs", json={
        "pack": pack,
        "run": run,
        "stage": "publish",
        "profile": "",
        "episode_name": episode_name,
    })
    if not submit_result.is_success:
        return _publish_redirect(job_id,
                                 error=f"failed to submit publish job: "
                                       f"{_extract_detail(submit_result)}")
    publish_job_id = submit_result.json().get("id")

    # 3. Wait for it to finish (bounded).
    pub_job = await _wait_for_job_terminal(
        http_client, publish_job_id, _PUBLISH_WAIT_BUDGET_S, _PUBLISH_POLL_INTERVAL_S)
    if pub_job is None:
        # Redirect to the publish JOB's own page (it will keep moving; the
        # operator can retry the upload from there once it completes).
        return RedirectResponse(
            url=f"/job/{publish_job_id}?publish_error="
                + quote(f"publish job still running after "
                        f"{int(_PUBLISH_WAIT_BUDGET_S)}s — it is still working, "
                        f"check that job page"),
            status_code=303)
    if pub_job.get("status") != "completed":
        return _publish_redirect(job_id,
                                 error=f"publish job failed: "
                                       f"{(pub_job.get('error') or str(pub_job))[:200]}")

    # 4. Upload the finished episode into the library.
    up = await http_client.post(f"/publish/{run}/upload",
                                params={"name": episode_name})
    if not up.is_success:
        return _publish_redirect(job_id,
                                 error=f"upload failed: {_extract_detail(up)}")
    result = pub_job.get("result") or {}
    return RedirectResponse(
        url=(f"/job/{job_id}?published=1"
             f"&episode_name={quote(episode_name)}"
             f"&event_count={result.get('event_count', '?')}"
             f"&control_panel_url={quote(CONTROL_PANEL_URL)}"),
        status_code=303)


def _publish_redirect(job_id: str, error: str) -> RedirectResponse:
    """Single place the publish flow's error banners come from — one
    query string, one percent-encoding rule, so a test only has to check
    the `publish_error` value in exactly one shape."""
    return RedirectResponse(url=f"/job/{job_id}?publish_error={quote(error)}",
                            status_code=303)


@app.post("/models/run", response_class=HTMLResponse)
async def run_model_ui(request: Request, model_url: str = Form(...)):
    """Trigger an ollama run/pull command with a normalized URL."""
    clean_url = model_url.strip().split('/')[-1]

    # NOTE: this is a placeholder — it does not actually shell out to
    # `ollama` (this container has no ollama binary). It only confirms
    # the URL normalization + form submission round trip works.
    return templates.TemplateResponse(
        request, "dashboard.html",
        _dashboard_context(
            request,
            jobs=[],
            message_result={"ok": True, "msg": f"Command triggered: ollama run {clean_url}"},
        )
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Data Viewer — browse generated output (arc plans, briefs, trees, dialogue)
# straight out of generation_artifacts. Thin proxy over generator-api's
# /runs, /artifacts, /artifacts/{id}, same pattern as the Saved
# Configurations section: this service does no parsing/validation of its
# own, it just relays whatever the generator API returns.
# ---------------------------------------------------------------------------

async def _fetch_runs():
    """Best-effort fetch of every output run for the run picker. Never
    raises — a generator-api hiccup degrades to an empty list with an
    error string, same pattern as _fetch_configs."""
    try:
        result = await http_client.get("/runs")
    except httpx.RequestError as exc:
        return [], f"generator-api unreachable: {exc}"
    if not result.is_success:
        return [], f"API error: {result.status_code}"
    try:
        runs = result.json()
        if not isinstance(runs, list):
            return [], "Failed to parse runs JSON"
        return runs, None
    except Exception:
        return [], "Failed to parse runs JSON"


@app.get("/data", response_class=HTMLResponse)
async def data_viewer(request: Request, run: str = ""):
    """Browse generated artifacts for one run. With no `run` selected,
    shows only the run picker."""
    runs, runs_error = await _fetch_runs()

    artifacts = []
    artifacts_error = None
    if run:
        try:
            result = await http_client.get("/artifacts", params={"run": run})
        except httpx.RequestError as exc:
            artifacts_error = f"generator-api unreachable: {exc}"
        else:
            if result.is_success:
                try:
                    artifacts = result.json()
                    if not isinstance(artifacts, list):
                        artifacts = []
                        artifacts_error = "Failed to parse artifacts JSON"
                except Exception:
                    artifacts_error = "Failed to parse artifacts JSON"
            else:
                artifacts_error = f"API error: {result.status_code}"

    return templates.TemplateResponse(
        request, "data_viewer.html",
        {
            "request": request,
            "runs": runs,
            "runs_error": runs_error,
            "selected_run": run,
            "artifacts": artifacts,
            "artifacts_error": artifacts_error,
        },
    )


@app.get("/data/artifact/{artifact_id}", response_class=HTMLResponse)
async def data_viewer_artifact(request: Request, artifact_id: int):
    """Full document view for one artifact (arc plan / brief / tree /
    dialogue), pretty-printed as JSON."""
    result = await http_client.get(f"/artifacts/{artifact_id}")
    if not result.is_success:
        return templates.TemplateResponse(
            request, "error.html", {"request": request, "error": result.text}
        )
    artifact = result.json()
    return templates.TemplateResponse(
        request, "data_viewer_artifact.html",
        {"request": request, "artifact": artifact},
    )


# ---------------------------------------------------------------------------
# Pack Viewer + Pack Editor — campaign pack content (campaign.yaml, cast,
# scenes, lore) as it lives in the generator database. This service stays a
# pure HTTP client of the generator API throughout: it holds no DB
# credentials and never touches the host filesystem, so browser-driven edits
# reach Postgres the same way every other mutation in this stack already
# does — an HTTP endpoint on the one service that already holds the store
# connection. (This is the revision the plan's first draft got wrong with a
# read-write bind mount.)
#
# The generator API is the single place that runs load_pack() and enforces
# the decision-4 pre-write gate; these routes just forward the form body and
# relay whatever status/detail comes back, re-rendering the same template
# with the error inline on failure so the operator never loses the pack they
# were editing.
# ---------------------------------------------------------------------------

def _extract_detail(result) -> str:
    try:
        return result.json().get("detail", result.text)
    except Exception:
        return result.text or f"HTTP {result.status_code}"


@app.get("/packs", response_class=HTMLResponse)
async def packs_list(request: Request):
    """Every pack name (from the generator DB via GET /packs), with a deep
    link into each one's full view. The dashboard's Pack dropdown already
    knows these names for job submission; this is the same list surfaced as
    its own section so an operator can get there without submitting a job
    first."""
    packs, packs_error = await _fetch_packs()
    return templates.TemplateResponse(
        request, "packs.html",
        {"request": request, "packs": packs, "packs_error": packs_error},
    )


@app.get("/packs/{pack_name}", response_class=HTMLResponse)
async def pack_viewer(request: Request, pack_name: str):
    """Full read-only view of a pack: campaign meta, cast (with worker_id),
    scenes (start/ambient/default_next), and lore note names. Sourced
    entirely from GET /packs/{pack_name} — the generator API materializes
    the rows, runs the real load_pack(), and returns a JSON projection;
    nothing here parses or validates the pack itself."""
    try:
        result = await http_client.get(f"/packs/{pack_name}")
    except httpx.RequestError as exc:
        return _render_pack_error(request, f"generator-api unreachable: {exc}")
    if not result.is_success:
        return _render_pack_error(request, _extract_detail(result))
    pack = result.json()
    return templates.TemplateResponse(
        request, "pack_viewer.html",
        {"request": request, "pack_name": pack_name, "pack": pack, "pack_error": None},
    )


def _render_pack_error(request: Request, error: str):
    return templates.TemplateResponse(
        request, "pack_viewer.html",
        {"request": request, "pack_name": None, "pack": None, "pack_error": error},
    )


@app.get("/packs/{pack_name}/scenes/{scene_id}", response_class=HTMLResponse)
async def pack_scene_detail(request: Request, pack_name: str, scene_id: str):
    """One scene's raw YAML, pre-filled into an editable form (Phase 2's
    editor lives on the same template, per the plan: the viewer and the
    editor share a surface, they only differ on whether a form was just
    posted). The parsed view is not rendered as separate structured fields
    — the plan's decision 2 settled on one raw-YAML textarea per document,
    with the generator API's 422 diagnostic (carrying the real load_pack
    error) as the error path a bad edit hits, rather than a client-side
    per-field form for every optional beat/branch field."""
    try:
        result = await http_client.get(f"/packs/{pack_name}/scenes/{scene_id}")
    except httpx.RequestError as exc:
        return _render_scene_error(request, f"generator-api unreachable: {exc}")
    if not result.is_success:
        return _render_scene_error(request, _extract_detail(result))
    data = result.json()
    return templates.TemplateResponse(
        request, "pack_scene_detail.html",
        {
            "request": request,
            "pack_name": pack_name,
            "scene_id": scene_id,
            "scene_yaml": data["scene_yaml"],
            "save_error": None,
            "save_ok": None,
        },
    )


def _render_scene_error(request: Request, error: str):
    return templates.TemplateResponse(
        request, "pack_scene_detail.html",
        {"request": request, "pack_name": None, "scene_id": None,
         "scene_yaml": "", "save_error": error, "save_ok": None},
    )


@app.post("/packs/{pack_name}/scenes/{scene_id}", response_class=HTMLResponse)
async def save_pack_scene(
    request: Request,
    pack_name: str,
    scene_id: str,
    scene_yaml: str = Form(...),
):
    """Create or replace one scene via PUT /packs/{pack_name}/scenes/{id}.
    Any 4xx comes back with the generator API's own `detail` — for a 422
    that's the real load_pack() diagnostic, so an operator sees exactly what
    would break, re-typed the text, and can immediately see the corrected
    form again (textarea re-populated from `scene_yaml`) rather than
    being dropped to a bare error page."""
    payload = {"scene_yaml": scene_yaml}
    try:
        result = await http_client.put(f"/packs/{pack_name}/scenes/{scene_id}",
                                       json=payload)
    except httpx.RequestError as exc:
        return _render_scene_error(request, f"generator-api unreachable: {exc}")

    if result.is_success:
        return templates.TemplateResponse(
            request, "pack_scene_detail.html",
            {"request": request, "pack_name": pack_name, "scene_id": scene_id,
             "scene_yaml": scene_yaml, "save_error": None, "save_ok": True},
        )
    return templates.TemplateResponse(
        request, "pack_scene_detail.html",
        {"request": request, "pack_name": pack_name, "scene_id": scene_id,
         "scene_yaml": scene_yaml, "save_error": _extract_detail(result),
         "save_ok": None},
    )


@app.get("/packs/{pack_name}/cast/{member_id}", response_class=HTMLResponse)
async def pack_cast_member_detail(request: Request, pack_name: str, member_id: str):
    try:
        result = await http_client.get(f"/packs/{pack_name}/cast/{member_id}")
    except httpx.RequestError as exc:
        return _render_cast_error(request, f"generator-api unreachable: {exc}")
    if not result.is_success:
        return _render_cast_error(request, _extract_detail(result))
    data = result.json()
    return templates.TemplateResponse(
        request, "pack_cast_detail.html",
        {"request": request, "pack_name": pack_name, "member_id": member_id,
         "member_yaml": data["member_yaml"], "worker_id": data.get("worker_id") or "",
         "save_error": None, "save_ok": None},
    )


def _render_cast_error(request: Request, error: str):
    return templates.TemplateResponse(
        request, "pack_cast_detail.html",
        {"request": request, "pack_name": None, "member_id": None,
         "member_yaml": "", "worker_id": "", "save_error": error, "save_ok": None},
    )


@app.post("/packs/{pack_name}/cast/{member_id}", response_class=HTMLResponse)
async def save_pack_cast_member(
    request: Request,
    pack_name: str,
    member_id: str,
    member_yaml: str = Form(...),
    worker_id: str = Form(""),
):
    payload = {"member_yaml": member_yaml, "worker_id": worker_id or None}
    try:
        result = await http_client.put(f"/packs/{pack_name}/cast/{member_id}",
                                       json=payload)
    except httpx.RequestError as exc:
        return _render_cast_error(request, f"generator-api unreachable: {exc}")

    if result.is_success:
        return templates.TemplateResponse(
            request, "pack_cast_detail.html",
            {"request": request, "pack_name": pack_name, "member_id": member_id,
             "member_yaml": member_yaml, "worker_id": worker_id,
             "save_error": None, "save_ok": True},
        )
    return templates.TemplateResponse(
        request, "pack_cast_detail.html",
        {"request": request, "pack_name": pack_name, "member_id": member_id,
         "member_yaml": member_yaml, "worker_id": worker_id,
         "save_error": _extract_detail(result), "save_ok": None},
    )


@app.get("/packs/{pack_name}/lore/{lore_name}", response_class=HTMLResponse)
async def pack_lore_detail(request: Request, pack_name: str, lore_name: str):
    try:
        result = await http_client.get(f"/packs/{pack_name}/lore/{lore_name}")
    except httpx.RequestError as exc:
        return _render_lore_error(request, f"generator-api unreachable: {exc}")
    if not result.is_success:
        return _render_lore_error(request, _extract_detail(result))
    data = result.json()
    return templates.TemplateResponse(
        request, "pack_lore_detail.html",
        {"request": request, "pack_name": pack_name, "lore_name": lore_name,
         "lore_text": data["lore_text"], "save_error": None, "save_ok": None},
    )


def _render_lore_error(request: Request, error: str):
    return templates.TemplateResponse(
        request, "pack_lore_detail.html",
        {"request": request, "pack_name": None, "lore_name": None,
         "lore_text": "", "save_error": error, "save_ok": None},
    )


@app.post("/packs/{pack_name}/lore/{lore_name}", response_class=HTMLResponse)
async def save_pack_lore(
    request: Request,
    pack_name: str,
    lore_name: str,
    lore_text: str = Form(...),
):
    try:
        result = await http_client.put(f"/packs/{pack_name}/lore/{lore_name}",
                                       json={"lore_text": lore_text})
    except httpx.RequestError as exc:
        return _render_lore_error(request, f"generator-api unreachable: {exc}")

    if result.is_success:
        return templates.TemplateResponse(
            request, "pack_lore_detail.html",
            {"request": request, "pack_name": pack_name, "lore_name": lore_name,
             "lore_text": lore_text, "save_error": None, "save_ok": True},
        )
    return templates.TemplateResponse(
        request, "pack_lore_detail.html",
        {"request": request, "pack_name": pack_name, "lore_name": lore_name,
         "lore_text": lore_text, "save_error": _extract_detail(result),
         "save_ok": None},
    )


# ---------------------------------------------------------------------------
# Saved Configurations — thin proxy over the generator-api /configs surface.
# This service does no YAML validation of its own; the generator API is the
# single source of truth for what a valid config looks like (it actually
# has to resolve model profiles against it), so every route here just
# forwards the body and relays whatever status/detail comes back.
# ---------------------------------------------------------------------------
@app.get("/partials/configs", response_class=HTMLResponse)
async def partial_configs(request: Request):
    """HTMX refresh target for just the Saved Configurations card."""
    configs, configs_error = await _fetch_configs()
    return templates.TemplateResponse(
        request, "_configs_section.html",
        {"request": request, "configs": configs, "configs_error": configs_error, "banner": None}
    )


@app.get("/configs/new", response_class=HTMLResponse)
async def new_config_form(request: Request):
    """Blank editor for creating a saved config from scratch."""
    return templates.TemplateResponse(
        request, "config_editor.html",
        {"request": request, "config": None, "error": None}
    )


@app.get("/configs/{config_id}/edit", response_class=HTMLResponse)
async def edit_config_form(request: Request, config_id: int):
    """Editor pre-filled with one saved config's current name/description/YAML."""
    result = await http_client.get(f"/configs/{config_id}")
    if not result.is_success:
        return templates.TemplateResponse(
            request, "error.html", {"request": request, "error": result.text}
        )
    return templates.TemplateResponse(
        request, "config_editor.html",
        {"request": request, "config": result.json(), "error": None}
    )


@app.post("/configs/new", response_class=HTMLResponse)
async def create_config_ui(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    config_yaml: str = Form(...),
):
    """Save a brand new named config."""
    result = await http_client.post("/configs", json={
        "name": name, "description": description, "config_yaml": config_yaml,
    })
    if result.is_success:
        return RedirectResponse(url="/", status_code=303)
    # Re-render the editor with what the user typed plus the error, rather
    # than a bare error page — losing a hand-edited YAML document on a
    # rejected save (e.g. duplicate name) would be a genuinely bad
    # experience for anything past a one-line edit.
    return templates.TemplateResponse(
        request, "config_editor.html",
        {"request": request,
         "config": {"id": None, "name": name, "description": description, "config_yaml": config_yaml},
         "error": _extract_error_detail(result)}
    )


@app.post("/configs/{config_id}/edit", response_class=HTMLResponse)
async def update_config_ui(
    request: Request,
    config_id: int,
    description: str = Form(""),
    config_yaml: str = Form(...),
):
    """Save edits to an existing config. Name is immutable (see
    generation_store.update_config's docstring) — the form doesn't even
    submit it."""
    result = await http_client.put(f"/configs/{config_id}", json={
        "description": description, "config_yaml": config_yaml,
    })
    if result.is_success:
        return RedirectResponse(url="/", status_code=303)
    existing = await http_client.get(f"/configs/{config_id}")
    name = existing.json().get("name", "") if existing.is_success else ""
    return templates.TemplateResponse(
        request, "config_editor.html",
        {"request": request,
         "config": {"id": config_id, "name": name, "description": description, "config_yaml": config_yaml},
         "error": _extract_error_detail(result)}
    )


@app.post("/configs/{config_id}/activate", response_class=HTMLResponse)
async def activate_config_ui(request: Request, config_id: int):
    """Switch the live generator config to this saved one. Returns just the
    Saved Configurations partial so HTMX can swap it in place without a
    full page reload."""
    result = await http_client.post(f"/configs/{config_id}/activate")
    configs, configs_error = await _fetch_configs()
    banner = None
    if result.is_success:
        banner = {"ok": True, "msg": f"Activated {result.json().get('name', config_id)!r}"}
    else:
        banner = {"ok": False, "msg": _extract_error_detail(result)}
    return templates.TemplateResponse(
        request, "_configs_section.html",
        {"request": request, "configs": configs, "configs_error": configs_error, "banner": banner}
    )


@app.post("/configs/{config_id}/delete", response_class=HTMLResponse)
async def delete_config_ui(request: Request, config_id: int):
    """Remove a saved config. Returns the refreshed partial, same pattern
    as activate."""
    result = await http_client.delete(f"/configs/{config_id}")
    configs, configs_error = await _fetch_configs()
    banner = None if result.is_success else {"ok": False, "msg": _extract_error_detail(result)}
    return templates.TemplateResponse(
        request, "_configs_section.html",
        {"request": request, "configs": configs, "configs_error": configs_error, "banner": banner}
    )


def _extract_error_detail(result: httpx.Response) -> str:
    """FastAPI's HTTPException responses are {"detail": "..."} — pull that
    out when present, fall back to the raw body so nothing is ever silently
    dropped."""
    try:
        data = result.json()
        if isinstance(data, dict) and "detail" in data:
            return str(data["detail"])
    except Exception:
        pass
    return result.text


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@app.on_event("shutdown")
async def shutdown_event():
    await http_client.aclose()
