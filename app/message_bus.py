"""
message_bus.py
Shared Kafka message-envelope, producer, and consumer helpers used by
agent.py, tail_bus.py, and the message-logger / message-api services.
"""
import json
import os
import time
import uuid
from datetime import datetime, timezone

import yaml
from kafka import KafkaProducer, KafkaConsumer

BROADCAST = "broadcast"


def load_worker_config(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def resolve(env_name, config_value, default=None):
    """Env var wins over a worker config's value, which wins over `default`.
    The single source of truth for env-vs-config precedence — every reader of
    message_bus/Postgres connection details (agent.py, replay_pane.py, ...)
    must go through this, or a value can silently diverge between them when
    an env var override isn't mirrored into config/workers/*.yaml (see
    docs/duet_replay.md)."""
    return os.environ.get(env_name) or config_value or default


def build_message(from_, to, type_, payload=None, correlation_id=None, causation_id=None):
    """Build a bus envelope.

    Correlation (docs/message_bus.md "Correlation IDs"): every message
    carries `correlation_id` — the id shared by a whole causal chain (one
    task, including its bug/fix retries) — and `causation_id` — the `id` of
    the message that directly caused this one, or None. A message that
    starts a new chain (correlation_id=None) uses its own `id` as the
    correlation id. Handlers replying to a message should pass
    `**reply_ids(msg)` instead of computing these by hand.
    """
    msg_id = str(uuid.uuid4())
    return {
        "id": msg_id,
        "from": from_,
        "to": to,
        "type": type_,
        "payload": payload or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id or msg_id,
        "causation_id": causation_id,
    }


def correlation_of(msg):
    """The chain id of `msg`: its `correlation_id`, falling back to its own
    `id` for messages from older senders that predate correlation IDs (and
    None when `msg` has neither, e.g. a hand-built test dict)."""
    if not isinstance(msg, dict):
        return None
    return msg.get("correlation_id") or msg.get("id")


def reply_ids(msg):
    """Keyword args for build_message() when sending a message *because of*
    `msg`: same chain (correlation_id), caused by `msg` (causation_id).

        producer.send(build_message(me, to, "task_complete", payload, **reply_ids(msg)))

    Tolerates a missing/None `msg` or one without an `id` — the new message
    then simply starts its own chain.
    """
    if not isinstance(msg, dict):
        return {"correlation_id": None, "causation_id": None}
    return {"correlation_id": correlation_of(msg), "causation_id": msg.get("id")}


class MessageProducer:
    def __init__(self, bootstrap_servers, topic):
        self.topic = topic
        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

    def send(self, message):
        self._producer.send(self.topic, value=message)
        self._producer.flush()
        return message


class MessageConsumer:
    def __init__(self, bootstrap_servers, topic, group_id, worker_id=None):
        self.worker_id = worker_id
        self._consumer = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
            consumer_timeout_ms=1000,
        )

    def poll_new(self, to_filter=True):
        messages = []
        for record in self._consumer:
            msg = record.value
            if to_filter and self.worker_id:
                if msg.get("to") not in (self.worker_id, BROADCAST):
                    continue
            messages.append(msg)
        return messages

    def __iter__(self):
        # consumer_timeout_ms means the underlying iterator ends when idle;
        # loop forever so callers get a continuous stream instead.
        while True:
            for record in self._consumer:
                yield record.value
