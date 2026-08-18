#!/usr/bin/env python3
"""
panel.py
Browser control panel for services/message-api. Server-rendered FastAPI +
Jinja2 + HTMX (no build step, no Node) — every route here is a thin wrapper
around one message-api HTTP call, so this service never touches Redis,
Kafka, or Postgres directly. That mirrors the trust boundary message-api's
own module docstring already describes: this is just a friendlier client of
the same HTTP surface docs/message_api.md documents for curl.

MESSAGE_API_URL (default http://message-api:8000) points this at message-api,
the same env-var pattern services/twitch-presence uses for its own calls to
POST /messages.
"""
import base64
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

log = logging.getLogger("control_panel")
logging.basicConfig(level=logging.INFO, format="[control-panel] %(levelname)s %(message)s")

BASE_DIR = Path(__file__).resolve().parent
MESSAGE_API_URL = os.environ.get("MESSAGE_API_URL", "http://message-api:8000")

# Same hardcoded lists api.py's own WORKER_ID_EXAMPLES/MESSAGE_TYPE_EXAMPLES
# use, for the same reason: message-api exposes no "list workers" or "list
# message types" endpoint, and these can drift from docker-compose.yml.
WORKER_IDS = ["coder", "coder-native", "coder-opencode", "coder-aider", "manager", "tester"]
MESSAGE_TYPE_EXAMPLES = ["operator_message", "status_update", "coding_run_report"]

# In-memory only (module-level, resets on restart) — message-api has no
# "list filtered types" endpoint either, so the panel just tracks whichever
# types an operator has looked at/added this process's lifetime, seeded with
# the one type log_filter_control.py excludes by default.
KNOWN_LOG_TYPES = ["status_update"]

# ── Optional HTTP Basic Auth — no-ops unless both vars are set ─────────────
BASIC_AUTH_USER = os.environ.get("CONTROL_PANEL_BASIC_AUTH_USER", "")
BASIC_AUTH_PASS = os.environ.get("CONTROL_PANEL_BASIC_AUTH_PASS", "")


def _auth_enabled() -> bool:
    return bool(BASIC_AUTH_USER and BASIC_AUTH_PASS)


http_client = httpx.AsyncClient(base_url=MESSAGE_API_URL, timeout=10.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup message_api_url=%s auth_enabled=%s", MESSAGE_API_URL, _auth_enabled())
    yield
    await http_client.aclose()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
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
        headers={"WWW-Authenticate": 'Basic realm="control-panel"'},
    )


class MapiResult:
    """Normalized outcome of one message-api call — every route branches on
    `.ok` rather than juggling httpx exceptions and HTTP status codes itself."""

    def __init__(self, ok: bool, status_code: int, data: Any = None, error: Optional[str] = None):
        self.ok = ok
        self.status_code = status_code
        self.data = data
        self.error = error


async def _mapi_request(method: str, path: str, **kwargs) -> MapiResult:
    try:
        resp = await http_client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        log.error("message-api unreachable method=%s path=%s error=%s", method, path, exc)
        return MapiResult(ok=False, status_code=0, error=f"message-api unreachable: {exc}")

    data = None
    if resp.content:
        try:
            data = resp.json()
        except ValueError:
            data = None

    if resp.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        error = detail or f"message-api returned HTTP {resp.status_code}"
        log.warning("message-api error method=%s path=%s status=%s detail=%s", method, path, resp.status_code, error)
        return MapiResult(ok=False, status_code=resp.status_code, data=data, error=error)

    return MapiResult(ok=True, status_code=resp.status_code, data=data)


async def _worker_status(worker_id: str) -> dict:
    result = await _mapi_request("GET", f"/workers/{worker_id}")
    if result.ok:
        return {"id": worker_id, "enabled": result.data.get("enabled"), "error": None}
    return {"id": worker_id, "enabled": None, "error": result.error}


async def _log_filter_status(message_type: str) -> dict:
    result = await _mapi_request("GET", f"/log-filter/{message_type}")
    if result.ok:
        return {"type": message_type, "excluded": result.data.get("excluded"), "error": None}
    return {"type": message_type, "excluded": None, "error": result.error}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    workers = [await _worker_status(w) for w in WORKER_IDS]
    log_types = [await _log_filter_status(t) for t in KNOWN_LOG_TYPES]
    replays_result = await _mapi_request("GET", "/replays")
    replays = replays_result.data.get("episodes", []) if replays_result.ok else []
    return templates.TemplateResponse(request, "base.html", {
        "message_api_url": MESSAGE_API_URL,
        "workers": workers,
        "message_type_examples": MESSAGE_TYPE_EXAMPLES,
        "worker_ids": WORKER_IDS,
        "log_types": log_types,
        "replays": replays,
        "replays_error": None if replays_result.ok else replays_result.error,
        "message_result": None,
        "prune_result": None,
    })


# ── Workers ──────────────────────────────────────────────────────────────
@app.get("/partials/workers", response_class=HTMLResponse)
async def partial_workers(request: Request):
    workers = [await _worker_status(w) for w in WORKER_IDS]
    return templates.TemplateResponse(request, "_workers_table.html", {"workers": workers})


async def _set_worker(request: Request, worker_id: str, enabled: bool):
    result = await _mapi_request("POST", f"/workers/{worker_id}/{'enable' if enabled else 'disable'}")
    if result.ok:
        worker = {"id": worker_id, "enabled": result.data.get("enabled"), "error": None}
    else:
        worker = {"id": worker_id, "enabled": None, "error": result.error}
    return templates.TemplateResponse(request, "_worker_row.html", {"worker": worker})


@app.post("/workers/{worker_id}/enable", response_class=HTMLResponse)
async def enable_worker(request: Request, worker_id: str):
    return await _set_worker(request, worker_id, True)


@app.post("/workers/{worker_id}/disable", response_class=HTMLResponse)
async def disable_worker(request: Request, worker_id: str):
    return await _set_worker(request, worker_id, False)


# ── Log filter ───────────────────────────────────────────────────────────
@app.post("/log-filter/add", response_class=HTMLResponse)
async def add_log_filter_type(request: Request, message_type: str = Form(...)):
    message_type = message_type.strip()
    if message_type and message_type not in KNOWN_LOG_TYPES and len(message_type) <= 128:
        KNOWN_LOG_TYPES.append(message_type)
    log_types = [await _log_filter_status(t) for t in KNOWN_LOG_TYPES]
    return templates.TemplateResponse(request, "_log_filter_table.html", {"log_types": log_types})


async def _set_log_filter(request: Request, message_type: str, excluded: bool):
    result = await _mapi_request(
        "POST", f"/log-filter/{message_type}/{'exclude' if excluded else 'include'}")
    if result.ok:
        row = {"type": message_type, "excluded": result.data.get("excluded"), "error": None}
    else:
        row = {"type": message_type, "excluded": None, "error": result.error}
    return templates.TemplateResponse(request, "_log_filter_row.html", {"log_type": row})


@app.post("/log-filter/{message_type}/exclude", response_class=HTMLResponse)
async def exclude_log_type(request: Request, message_type: str):
    return await _set_log_filter(request, message_type, True)


@app.post("/log-filter/{message_type}/include", response_class=HTMLResponse)
async def include_log_type(request: Request, message_type: str):
    return await _set_log_filter(request, message_type, False)


# ── Message composer ────────────────────────────────────────────────────
@app.post("/messages", response_class=HTMLResponse)
async def send_message(
    request: Request,
    to: str = Form(...),
    msg_type: str = Form("operator_message", alias="type"),
    payload: str = Form("{}"),
):
    try:
        payload_obj = json.loads(payload) if payload.strip() else {}
        if not isinstance(payload_obj, dict):
            raise ValueError("payload must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        return templates.TemplateResponse(request, "_message_result.html", {
            "message_result": {"ok": False, "error": f"payload is not a valid JSON object: {exc}"},
        })

    result = await _mapi_request("POST", "/messages", json={"to": to, "type": msg_type, "payload": payload_obj})
    if result.ok:
        message_result = {"ok": True, "sent": result.data}
    else:
        message_result = {"ok": False, "error": result.error}
    return templates.TemplateResponse(request, "_message_result.html", {"message_result": message_result})


# ── Log pruning ──────────────────────────────────────────────────────────
@app.post("/logs/prune", response_class=HTMLResponse)
async def prune_logs(request: Request, after: str = Form(""), before: str = Form("")):
    body = {}
    if after.strip():
        body["after"] = after.strip()
    if before.strip():
        body["before"] = before.strip()
    result = await _mapi_request("POST", "/logs/prune", json=body)
    if result.ok:
        prune_result = {"ok": True, "deleted": result.data.get("deleted")}
    else:
        prune_result = {"ok": False, "error": result.error}
    return templates.TemplateResponse(request, "_prune_result.html", {"prune_result": prune_result})


# ── Rerun Theater replays ───────────────────────────────────────────────
async def _replays_section_context(banner: Optional[dict] = None) -> dict:
    result = await _mapi_request("GET", "/replays")
    replays = result.data.get("episodes", []) if result.ok else []
    return {
        "replays": replays,
        "replays_error": None if result.ok else result.error,
        "upload_result": banner,
    }


@app.get("/partials/replays", response_class=HTMLResponse)
async def partial_replays(request: Request):
    return templates.TemplateResponse(request, "_replays_section.html", await _replays_section_context())


@app.post("/replays/upload", response_class=HTMLResponse)
async def upload_replay(
    request: Request,
    file: UploadFile,
    name: str = Form(""),
    overwrite: bool = Form(False),
):
    body = await file.read()
    params = {"overwrite": "true" if overwrite else "false"}
    if name.strip():
        params["name"] = name.strip()
    result = await _mapi_request(
        "POST", "/replays", params=params, content=body,
        headers={"Content-Type": "application/json"},
    )
    if result.ok:
        banner = {"ok": True, "name": result.data.get("name"), "event_count": result.data.get("event_count")}
    else:
        banner = {"ok": False, "error": result.error}
    return templates.TemplateResponse(
        request, "_replays_section.html", await _replays_section_context(banner))


@app.post("/replays/{name}/delete", response_class=HTMLResponse)
async def delete_replay(request: Request, name: str):
    result = await _mapi_request("DELETE", f"/replays/{name}")
    if result.ok:
        return HTMLResponse("")  # row's hx-swap="outerHTML" removes it from the DOM
    replay = {"name": name, "error": result.error}
    return templates.TemplateResponse(request, "_replay_row.html", {"replay": replay})


@app.get("/replays/{name}/view", response_class=HTMLResponse)
async def view_replay(request: Request, name: str):
    result = await _mapi_request("GET", f"/replays/{name}")
    if result.ok:
        pretty = json.dumps(result.data, indent=2)
        return templates.TemplateResponse(request, "_replay_view.html", {"pretty": pretty, "error": None})
    return templates.TemplateResponse(request, "_replay_view.html", {"pretty": None, "error": result.error})
