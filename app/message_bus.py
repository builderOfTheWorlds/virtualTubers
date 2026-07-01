"""
message_bus.py
Shared Kafka message-envelope, producer, and consumer helpers used by
agent.py, tail_bus.py, and the message-logger / message-api services.
"""
import json
import time
import uuid
from datetime import datetime, timezone

import yaml
from kafka import KafkaProducer, KafkaConsumer

BROADCAST = "broadcast"


def load_worker_config(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def build_message(from_, to, type_, payload=None):
    return {
        "id": str(uuid.uuid4()),
        "from": from_,
        "to": to,
        "type": type_,
        "payload": payload or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


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
