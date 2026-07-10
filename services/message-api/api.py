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
"""
import os

import redis
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel

from log_filter_control import LogFilterControl
from message_bus import build_message, MessageProducer
from worker_control import WorkerControl

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
