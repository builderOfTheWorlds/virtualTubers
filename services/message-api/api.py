#!/usr/bin/env python3
"""
api.py
Minimal HTTP interface for injecting test messages onto the Kafka bus, and
for turning individual workers on/off without a stack redeploy (see
worker_control.py), and for reading their health (GET /workers/health:
liveness key, the worker's self-reported local kill switch, enable flag).
Pure producer for /messages — no DB or filesystem writes; the message-logger
service handles durable logging independently.
Also exposes the /log-filter control endpoints — the HTTP surface for
excluding a noisy message type (e.g. the heartbeat status_update flood)
from message-logger's Postgres writes without a stack redeploy (see
worker_control.py and log_filter_control.py).
And the /replays endpoints — the only way an episode enters the Rerun
Theater library. Episodes used to be files hand-copied onto the deploy
host; they are now uploaded here, validated (episode_validator.py) and
stored in Postgres (episode_store.py), which is where the workers read
them from. An upload may be marked `?status=draft` (the 3layer-generator's
opt-in auto-submit does this): a draft is stored but never airs until a
human approves it via POST /replays/{name}/approve (the control panel's
"Drafts awaiting review" list). Rejecting a draft is the existing DELETE.
"""
import json
import logging
import os
from datetime import datetime
from typing import Literal, Optional

import psycopg2
import redis
from fastapi import FastAPI, HTTPException, Path, Query, Request
from pydantic import BaseModel

import episode_store
from console_theme import ConsoleThemeControl, get_theme, load_themes, theme_exists
from episode_validator import EpisodeInvalid, resolve_name, validate_episode
from log_filter_control import LogFilterControl
from log_prune import prune_logs
from message_bus import build_message, MessageProducer
from replay_logs import fetch_container_logs, fetch_messages
from worker_control import WorkerControl

log = logging.getLogger("message_api")

app = FastAPI()
producer = MessageProducer(
    bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"],
    topic=os.environ["KAFKA_TOPIC"],
)
control = WorkerControl.from_config()
log_filter = LogFilterControl.from_config()
console_theme = ConsoleThemeControl.from_config()

# Example message types shown as a dropdown in /docs; accepts any string.
MESSAGE_TYPE_EXAMPLES = {
    "status_update": {"value": "status_update"},
    "operator_message": {"value": "operator_message"},
    "coding_run_report": {"value": "coding_run_report"},
}

# WORKER_ID values assigned to each service in docker-compose.yml. Shown as a
# dropdown of examples in /docs — worker_id still accepts any string, since
# this list can drift from the compose file.
WORKER_ID_EXAMPLES = {
    "coder": {"value": "coder"},
    "coder-native": {"value": "coder-native"},
    "coder-opencode": {"value": "coder-opencode"},
    "coder-aider": {"value": "coder-aider"},
    "manager": {"value": "manager"},
    "tester": {"value": "tester"},
}


class InjectMessage(BaseModel):
    to: str
    type: str = "operator_message"
    payload: dict = {}


class SetThemeRequest(BaseModel):
    theme: str


class PruneLogsRequest(BaseModel):
    after: Optional[datetime] = None
    before: Optional[datetime] = None


# uvicorn imposes no body-size limit of its own, and every upload is held in
# memory and then dry-run rendered. The largest real episode is well under
# 1 MB, so 8 MB is generous while still bounding a hostile request.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# No long-running consumer owns replay_episodes the way message-logger owns
# messages, so this service creates it. Best-effort at import and retried on
# the first write after a failure — a Postgres that is down at container
# start must not stop /messages from working.
_schema_ready = False


def _ensure_schema():
    global _schema_ready
    if _schema_ready or not episode_store.available():
        return
    try:
        episode_store.ensure_schema()
        _schema_ready = True
    except Exception as exc:
        log.warning("replay_episodes schema not ready: %s: %s",
                    type(exc).__name__, exc)


_ensure_schema()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/messages")
def post_message(body: InjectMessage):
    message = build_message("operator", body.to, body.type, body.payload)
    producer.send(message)
    return message


# Health routes are declared BEFORE GET /workers/{worker_id} so the literal
# "health" segment isn't captured as a worker id. Never 503: a Redis outage
# reads as state "unknown" / alive null (see WorkerControl.health_many), and
# "enabled" is the raw Redis flag — never this container's kill file.
@app.get("/workers/health")
def get_all_workers_health():
    """Every known worker: the WORKER_ID_EXAMPLES list plus any id that has
    a liveness or enable key in Redis (e.g. roundtable, tuber_0)."""
    worker_ids = list(WORKER_ID_EXAMPLES)
    worker_ids += [w for w in control.known_worker_ids() if w not in worker_ids]
    rows = control.health_many(worker_ids)
    log.debug("event=workers_health count=%d unknown=%d", len(rows),
              sum(1 for r in rows if r["state"] == "unknown"))
    return {"workers": rows}


@app.get("/workers/{worker_id}/health")
def get_worker_health(worker_id: str = Path(..., openapi_examples=WORKER_ID_EXAMPLES)):
    return control.health(worker_id)


@app.get("/workers/{worker_id}")
def get_worker_status(
    worker_id: str = Path(..., openapi_examples=WORKER_ID_EXAMPLES),
):
    return {"worker_id": worker_id, "enabled": control.is_enabled(worker_id)}


@app.post("/workers/{worker_id}/enable")
def enable_worker(worker_id: str = Path(..., openapi_examples=WORKER_ID_EXAMPLES)):
    return _set_worker_enabled(worker_id, True)


@app.post("/workers/{worker_id}/disable")
def disable_worker(worker_id: str = Path(..., openapi_examples=WORKER_ID_EXAMPLES)):
    return _set_worker_enabled(worker_id, False)


def _set_worker_enabled(worker_id: str, enabled: bool):
    try:
        control.set_enabled(worker_id, enabled)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}")
    return {"worker_id": worker_id, "enabled": enabled}


@app.get("/log-filter/{message_type}")
def get_log_filter(message_type: str = Path(..., openapi_examples=MESSAGE_TYPE_EXAMPLES)):
    return {"type": message_type, "excluded": log_filter.is_excluded(message_type)}


@app.post("/log-filter/{message_type}/exclude")
def exclude_log_type(message_type: str = Path(..., openapi_examples=MESSAGE_TYPE_EXAMPLES)):
    return _set_log_filter(message_type, True)


@app.post("/log-filter/{message_type}/include")
def include_log_type(message_type: str = Path(..., openapi_examples=MESSAGE_TYPE_EXAMPLES)):
    return _set_log_filter(message_type, False)


def _set_log_filter(message_type: str, excluded: bool):
    try:
        log_filter.set_excluded(message_type, excluded)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}")
    return {"type": message_type, "excluded": excluded}


# ── Console theme control ────────────────────────────────────────────────────
# Gogh's full theme set (config/themes/gogh_themes.json, 1247 schemes) applied
# live to a worker's xterm via app/theme_watcher.py — see console_theme.py's
# module docstring for the full precedence/plumbing story.
@app.get("/console-themes")
def list_console_themes():
    """Every available theme name — what a `theme` value below must match."""
    return {"themes": sorted(load_themes().keys())}


@app.get("/console-theme/{worker_id}")
def get_console_theme(worker_id: str = Path(..., openapi_examples=WORKER_ID_EXAMPLES)):
    active = console_theme.get_theme_name(worker_id)
    return {"worker_id": worker_id, "theme": active, "overridden": active is not None}


@app.post("/console-theme/{worker_id}")
def set_console_theme(
    body: SetThemeRequest,
    worker_id: str = Path(..., openapi_examples=WORKER_ID_EXAMPLES),
):
    # Validate against the real theme set up front: a typo here should be a
    # 400 now, not a silent no-op fallback the next time theme_watcher polls.
    if not theme_exists(body.theme):
        raise HTTPException(status_code=404, detail=f"unknown theme {body.theme!r}")
    theme = get_theme(body.theme)
    try:
        console_theme.set_theme_name(worker_id, theme["name"])
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}")
    return {"worker_id": worker_id, "theme": theme["name"]}


@app.delete("/console-theme/{worker_id}")
def clear_console_theme(worker_id: str = Path(..., openapi_examples=WORKER_ID_EXAMPLES)):
    """Drop the live override so the worker falls back to its config file's
    `console.theme` (or the built-in default) on theme_watcher's next poll."""
    try:
        console_theme.clear_theme_name(worker_id)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}")
    return {"worker_id": worker_id, "theme": None, "overridden": False}


@app.post("/logs/prune")
def prune_logs_endpoint(body: PruneLogsRequest):
    if body.after is None and body.before is None:
        raise HTTPException(status_code=400, detail="at least one of after/before is required")
    try:
        deleted = prune_logs(after=body.after, before=body.before)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    return {"deleted": deleted, "after": body.after, "before": body.before}


# ── Log read endpoints (docs/replay_logs.md) ────────────────────────────────
# Read-only tails for the control-panel's Rerun Theater "Play" log viewer:
# container stdout/stderr (log-shipper's container_logs) and Kafka bus
# messages (message-logger's messages), each scoped to a caller-given
# identifier list plus an optional `since` timestamp so the panel can poll
# forward from when it started watching instead of re-fetching everything.
MAX_LOG_LIMIT = 500


@app.get("/logs/containers")
def get_container_logs(
    service: list[str] = Query(..., description="Compose service name(s), e.g. worker-coder"),
    since: Optional[datetime] = Query(None),
    limit: int = Query(200, ge=1, le=MAX_LOG_LIMIT),
):
    try:
        rows = fetch_container_logs(service, since=since, limit=limit)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    return {"logs": rows}


@app.get("/logs/messages")
def get_message_logs(
    worker_id: list[str] = Query(..., description="Worker id(s), e.g. coder, roundtable"),
    since: Optional[datetime] = Query(None),
    limit: int = Query(200, ge=1, le=MAX_LOG_LIMIT),
):
    try:
        rows = fetch_messages(worker_id, since=since, limit=limit)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    return {"messages": rows}


# ── Rerun Theater episode library ────────────────────────────────────────────
def _require_store():
    if not episode_store.available():
        raise HTTPException(
            status_code=503,
            detail="postgres unavailable: POSTGRES_* is not configured for this service")


def _safe_name(name: str) -> str:
    """Apply the validator's name rule to a path parameter, so a lookup or a
    delete can never be handed something the upload path would have refused."""
    try:
        return resolve_name(None, name)
    except EpisodeInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/replays")
async def upload_replay(
    request: Request,
    name: Optional[str] = Query(
        None, description="Override the episode key; defaults to the script's 'source'"),
    overwrite: bool = Query(
        False, description="Replace an episode of the same name instead of failing with 409"),
    status: Literal["approved", "draft"] = Query(
        "approved",
        description="'draft' holds the episode for review — it never airs until "
                    "POST /replays/{name}/approve. Default 'approved' airs immediately "
                    "(the behaviour every upload had before drafts existed)."),
    uploaded_by: str = Query(
        "operator", min_length=1, max_length=64,
        description="Free-text attribution stored on the row"),
):
    """Validate a pre-built episode script (scripts/build_replay_library.py)
    and store it in the library. The body is the raw episode JSON, so
    `curl --data-binary @episode.json` uploads one directly.

    Reads the body via `request.body()` rather than a `bytes = Body(...)`
    param on purpose: on fastapi>=0.14x, a `bytes`-typed Body param gets
    JSON-decoded before its own type validator runs whenever the client's
    Content-Type is application/json — which turned the documented
    `-H 'Content-Type: application/json' --data-binary @file` upload into a
    422, regardless of any `media_type=` hint passed to Body(). Reading
    straight from the ASGI request bypasses that per-field coercion
    entirely and always returns the raw bytes; json.loads() below is what
    actually parses it."""
    _require_store()
    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"episode is {len(body)} bytes, over the {MAX_UPLOAD_BYTES} byte limit")
    try:
        script = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"body is not valid json: {exc}")

    try:
        # Raises EpisodeInvalid on bad shape, a bad name, a leak-audit hit or
        # a failed dry-run render. Its message is operator-facing and, by
        # construction, never quotes episode content.
        info = validate_episode(script, name=name)
    except EpisodeInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    _ensure_schema()
    try:
        created = episode_store.save_episode(
            info["name"], script, overwrite=overwrite, status=status,
            uploaded_by=uploaded_by)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    if not created:
        raise HTTPException(
            status_code=409,
            detail=f"episode {info['name']!r} already exists — re-send with ?overwrite=true")
    log.info("stored episode name=%s events=%s bytes=%s overwrite=%s status=%s "
             "uploaded_by=%s", info["name"], info["event_count"], info["byte_size"],
             overwrite, status, uploaded_by)
    return {**info, "created": True, "status": status}


@app.get("/replays")
def list_replays(
    status: Literal["approved", "draft", "all"] = Query(
        "approved",
        description="'approved' (default) is the airable library; 'draft' is the "
                    "review queue; 'all' is every row"),
):
    _require_store()
    _ensure_schema()
    try:
        return {"episodes": episode_store.list_episodes_detailed(
            status=None if status == "all" else status)}
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")


@app.get("/replays/{name}")
def get_replay(name: str = Path(...)):
    """The full stored script, for debugging what a worker will perform and
    for reviewing a draft before approving it — so, unlike the workers' own
    read path, this one includes drafts."""
    _require_store()
    _ensure_schema()
    try:
        script = episode_store.load_episode(_safe_name(name), include_drafts=True)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    if script is None:
        raise HTTPException(status_code=404, detail=f"no episode named {name!r}")
    return script


@app.post("/replays/{name}/approve")
def approve_replay(name: str = Path(...)):
    """Promote a draft so it can air. Idempotent: approving an already
    approved episode is a 200 with previous_status 'approved'."""
    _require_store()
    _ensure_schema()
    safe = _safe_name(name)
    try:
        previous = episode_store.approve_episode(safe)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    if previous is None:
        raise HTTPException(status_code=404, detail=f"no episode named {name!r}")
    log.info("approved episode name=%s previous_status=%s", safe, previous)
    return {"name": safe, "status": "approved", "previous_status": previous}


@app.delete("/replays/{name}")
def delete_replay(name: str = Path(...)):
    """Remove an episode whatever its status — rejecting a draft is this."""
    _require_store()
    _ensure_schema()
    safe = _safe_name(name)
    try:
        deleted = episode_store.delete_episode(safe)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    return {"name": safe, "deleted": deleted}
