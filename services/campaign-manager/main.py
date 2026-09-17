
import os
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

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
            "artifacts_error": artifacts_error
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
