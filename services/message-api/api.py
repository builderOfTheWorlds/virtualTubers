#!/usr/bin/env python3
"""
api.py
Minimal HTTP interface for injecting test messages onto the Kafka bus, and
for turning individual workers on/off without a stack redeploy (see
worker_control.py).
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
them from.
"""
import json
import logging
import os
from datetime import datetime
from typing import Optional

import psycopg2
import redis
from fastapi import Body, FastAPI, HTTPException, Path, Query
from pydantic import BaseModel

import episode_store
from episode_validator import EpisodeInvalid, resolve_name, validate_episode
from log_filter_control import LogFilterControl
from log_prune import prune_logs
from message_bus import build_message, MessageProducer
from worker_control import WorkerControl

log = logging.getLogger("message_api")

app = FastAPI()
producer = MessageProducer(
    bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"],
    topic=os.environ["KAFKA_TOPIC"],
)
control = WorkerControl.from_config()
log_filter = LogFilterControl.from_config()

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


@app.post("/logs/prune")
def prune_logs_endpoint(body: PruneLogsRequest):
    if body.after is None and body.before is None:
        raise HTTPException(status_code=400, detail="at least one of after/before is required")
    try:
        deleted = prune_logs(after=body.after, before=body.before)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    return {"deleted": deleted, "after": body.after, "before": body.before}


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
def upload_replay(
    body: bytes = Body(..., media_type="application/json"),
    name: Optional[str] = Query(
        None, description="Override the episode key; defaults to the script's 'source'"),
    overwrite: bool = Query(
        False, description="Replace an episode of the same name instead of failing with 409"),
):
    """Validate a pre-built episode script (scripts/build_replay_library.py)
    and store it in the library. The body is the raw episode JSON, so
    `curl --data-binary @episode.json` uploads one directly."""
    _require_store()
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
            info["name"], script, overwrite=overwrite)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    if not created:
        raise HTTPException(
            status_code=409,
            detail=f"episode {info['name']!r} already exists — re-send with ?overwrite=true")
    log.info("stored episode name=%s events=%s bytes=%s overwrite=%s",
             info["name"], info["event_count"], info["byte_size"], overwrite)
    return {**info, "created": True}


@app.get("/replays")
def list_replays():
    _require_store()
    _ensure_schema()
    try:
        return {"episodes": episode_store.list_episodes_detailed()}
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")


@app.get("/replays/{name}")
def get_replay(name: str = Path(...)):
    """The full stored script, for debugging what a worker will perform."""
    _require_store()
    _ensure_schema()
    try:
        script = episode_store.load_episode(_safe_name(name))
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    if script is None:
        raise HTTPException(status_code=404, detail=f"no episode named {name!r}")
    return script


@app.delete("/replays/{name}")
def delete_replay(name: str = Path(...)):
    _require_store()
    _ensure_schema()
    safe = _safe_name(name)
    try:
        deleted = episode_store.delete_episode(safe)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"postgres unavailable: {exc}")
    return {"name": safe, "deleted": deleted}
