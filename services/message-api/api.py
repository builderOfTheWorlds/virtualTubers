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
Also exposes the /campaigns control endpoints — runtime persona assignment
for the generic worker-1..worker-8 fleet (CONTRACT.md §8,
docs/campaign_control.md, docs/blank_workers.md): which campaign is active
and which worker plays which speaker, backed by campaign_control.py. This
service NEVER reads a campaign's personas.yaml itself — it only ever stores
{campaign, speaker} pairs in Redis; each worker resolves its own persona doc
from its own mounted config (app/agent.py), which is what keeps this
service dumb.
"""
import os
from datetime import datetime
from typing import Dict, Optional

import psycopg2
import redis
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel

from campaign_control import CampaignControl
from log_filter_control import LogFilterControl
from log_prune import prune_logs
from message_bus import build_message, MessageProducer
from worker_control import WorkerControl

app = FastAPI()
producer = MessageProducer(
    bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"],
    topic=os.environ["KAFKA_TOPIC"],
)
control = WorkerControl.from_config()
log_filter = LogFilterControl.from_config()
campaign_control = CampaignControl.from_config()

# Example message types shown as a dropdown in /docs; accepts any string.
MESSAGE_TYPE_EXAMPLES = {
    "status_update": {"value": "status_update"},
    "operator_message": {"value": "operator_message"},
    "coding_run_report": {"value": "coding_run_report"},
}

# WORKER_ID values assigned by docker-compose.yml's generic worker fleet
# (docs/blank_workers.md: worker-1..worker-8, identical/interchangeable
# until a campaign casts a persona onto one). Shown as a dropdown of
# examples in /docs — worker_id still accepts any string, since this list
# can drift from the compose file. Genericized from the old hardcoded
# 6-persona-named dropdown (coder/coder-native/.../tester) now that persona
# names are no longer worker identities (recon_platform.md §5).
WORKER_ID_EXAMPLES = {
    "worker-1": {"value": "worker-1"},
    "worker-2": {"value": "worker-2"},
    "worker-3": {"value": "worker-3"},
    "worker-4": {"value": "worker-4"},
    "worker-5": {"value": "worker-5"},
    "worker-6": {"value": "worker-6"},
    "worker-7": {"value": "worker-7"},
    "worker-8": {"value": "worker-8"},
}

# Shipped config/campaigns/<name>/ directories. Same "examples only, any
# string accepted" looseness as WORKER_ID_EXAMPLES above.
CAMPAIGN_EXAMPLES = {
    "coder": {"value": "coder"},
    "dnd": {"value": "dnd"},
}


class InjectMessage(BaseModel):
    to: str
    type: str = "operator_message"
    payload: dict = {}


class PruneLogsRequest(BaseModel):
    after: Optional[datetime] = None
    before: Optional[datetime] = None


class StartCampaignRequest(BaseModel):
    cast: Dict[str, str]
    force: bool = False


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


def _is_airing(worker_id: str) -> bool:
    """Mid-airing guard read (CONTRACT.md §8): worker:{id}:airing, written
    by app/replay_pane.py for the duration of one performance (set at
    airing start, cleared in a `finally` so a crashed show can't wedge a
    worker un-reassignable forever). Fails OPEN — absent/unreachable means
    "not airing" — so a Redis hiccup here can never block a legitimate
    campaign (re)assignment; reused control._client rather than a fourth
    Redis-wrapper class for one flag."""
    try:
        value = control._client.get(f"worker:{worker_id}:airing")
    except redis.RedisError as exc:
        print(f"[message-api] WARN redis unreachable checking airing state for {worker_id}: {exc}")
        return False
    return value == "1"


@app.post("/campaigns/{campaign}/start")
def start_campaign(
    body: StartCampaignRequest,
    campaign: str = Path(..., openapi_examples=CAMPAIGN_EXAMPLES),
):
    airing = sorted({worker_id for worker_id in body.cast.values() if _is_airing(worker_id)})
    if airing and not body.force:
        raise HTTPException(
            status_code=409,
            detail=f"worker(s) currently mid-airing, refusing without force=true: {airing}",
        )
    try:
        active = campaign_control.start(campaign, body.cast, force=body.force)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}")
    return {"campaign": active["campaign"], "cast": active["cast"]}


@app.post("/campaigns/stop")
def stop_campaign():
    try:
        previous = campaign_control.get_active()
        campaign_control.stop()
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}")
    return {"stopped": True, "campaign": (previous or {}).get("campaign")}


@app.get("/campaigns/active")
def get_active_campaign():
    active = campaign_control.get_active()
    if not active:
        return {"campaign": None}
    return {"campaign": active.get("campaign"), "cast": active.get("cast", {})}


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
