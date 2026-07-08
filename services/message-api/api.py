#!/usr/bin/env python3
"""
api.py
Minimal HTTP interface for injecting test messages onto the Kafka bus, and
for turning individual workers on/off without a stack redeploy (see
worker_control.py).
Pure producer for /messages — no DB or filesystem writes; the message-logger
service handles durable logging independently.
"""
import os

import redis
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel

from message_bus import build_message, MessageProducer
from worker_control import WorkerControl

app = FastAPI()
producer = MessageProducer(
    bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"],
    topic=os.environ["KAFKA_TOPIC"],
)
control = WorkerControl.from_config()

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
