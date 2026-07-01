# message_bus.py

## Overview

Shared Kafka message-bus helper used by `app/agent.py`, `app/tail_bus.py`, and
the `message-logger`/`message-api` services. Centralizes the JSON message
envelope shape and thin producer/consumer wrappers so each of those processes
doesn't reimplement Kafka client setup and (de)serialization.

## Signature

```python
def load_worker_config(path: str) -> dict
def build_message(from_: str, to: str, type_: str, payload: dict | None = None) -> dict

class MessageProducer:
    def __init__(self, bootstrap_servers: str, topic: str)
    def send(self, message: dict) -> dict

class MessageConsumer:
    def __init__(self, bootstrap_servers: str, topic: str, group_id: str, worker_id: str | None = None)
    def poll_new(self, to_filter: bool = True) -> list[dict]
    def __iter__(self) -> Iterator[dict]
```

## Parameters

- `path` (str, required) — filesystem path to a worker's YAML config file.
- `from_`/`to` (str, required) — worker IDs (`coder`/`manager`/`tester`/`operator`) or `broadcast` for `to`.
- `type_` (str, required) — message type, e.g. `task_assignment`, `status_update`, `operator_message`.
- `payload` (dict, optional, default `{}`) — free-form, type-specific data.
- `bootstrap_servers` (str, required) — Kafka bootstrap servers, e.g. `192.168.1.120:9092`.
- `topic` (str, required) — the single shared bus topic, e.g. `vtuber.messages`.
- `group_id` (str, required) — consumer group ID; must be unique per logical consumer (see `docs/VTuber_AI_Dev_Team_Concept.md` for the naming convention) so consumers don't steal each other's messages.
- `worker_id` (str, optional) — when set, `poll_new(to_filter=True)` only returns messages addressed to this worker or to `broadcast`.

## Return Value

- `load_worker_config` — parsed YAML as a `dict`.
- `build_message` — a `dict` with `id` (uuid4), `from`, `to`, `type`, `payload`, `timestamp` (ISO-8601 UTC) keys.
- `MessageProducer.send` — the message dict that was sent (after a synchronous flush).
- `MessageConsumer.poll_new` — a list of message dicts received within the consumer's poll window (empty if none).
- `MessageConsumer.__iter__` — an infinite generator yielding message dicts as they arrive.

## Dependencies

- `kafka-python` (`kafka.KafkaProducer`, `kafka.KafkaConsumer`)
- `pyyaml`
- Python standard library: `json`, `uuid`, `datetime`

## Usage Examples

```python
from message_bus import load_worker_config, build_message, MessageProducer, MessageConsumer

config = load_worker_config("/config/worker.yaml")
bus = config["message_bus"]

producer = MessageProducer(bus["bootstrap_servers"], bus["topic"])
producer.send(build_message("coder", "manager", "task_complete", {"ticket": 42}))

consumer = MessageConsumer(bus["bootstrap_servers"], bus["topic"], group_id="vtuber-agent-coder", worker_id="coder")
for msg in consumer.poll_new():
    print(msg["type"], msg["payload"])
```

```python
# Continuous consumption (used by the message-logger service)
consumer = MessageConsumer(bootstrap_servers, topic, group_id="vtuber-logger")
for msg in consumer:
    persist(msg)
```

## Error Handling

- `load_worker_config` raises `FileNotFoundError` if the config path doesn't exist, or `yaml.YAMLError` on malformed YAML — callers don't catch these; a missing/broken config is a fatal startup error for the process.
- `MessageProducer`/`MessageConsumer` construction raises `kafka.errors.NoBrokersAvailable` if `bootstrap_servers` is unreachable — intentionally left uncaught so the container fails fast and Docker's `restart: unless-stopped` policy retries.

## Changelog

- v1.0.0 (2026-07-01) — Initial version: JSON envelope, producer/consumer wrappers, config loader. Replaces the file-based `/data/world-state/messages/bus.log` bus with Kafka.
