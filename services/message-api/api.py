#!/usr/bin/env python3
"""
api.py
Minimal HTTP interface for injecting test messages onto the Kafka bus.
Pure producer — no DB or filesystem writes; the message-logger service
handles durable logging independently.
"""
import os

from fastapi import FastAPI
from pydantic import BaseModel

from message_bus import build_message, MessageProducer

app = FastAPI()
producer = MessageProducer(
    bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"],
    topic=os.environ["KAFKA_TOPIC"],
)


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
