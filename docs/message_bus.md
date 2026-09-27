# message_bus.py

## Overview

Shared Kafka message-bus helper used by `app/agent.py`, `app/tail_bus.py`, and
the `message-logger`/`message-api` services. Centralizes the JSON message
envelope shape and thin producer/consumer wrappers so each of those processes
doesn't reimplement Kafka client setup and (de)serialization.

> **Viewing the bus on stream.** The tmux "agent chat" pane is now the rich,
> configurable **`kafka_feed`** panel: it consumes every message on the topic and
> renders a colorized, filterable feed (`HH:MM:SS from ──▶ to  type  payload`),
> hiding the per-tick heartbeat flood by default. See
> [docs/message_bus_feed.md](message_bus_feed.md) for how it renders and
> [docs/panels.md](panels.md) for its `content:` config schema.

## Correlation IDs

Every envelope carries two chain fields (added v1.1.0):

| Field | Meaning |
|---|---|
| `correlation_id` | Shared by every message caused, directly or transitively, by one chain starter. A dev-team task — `task_assignment` → `coding_run_report` / `task_complete` / `commit_notification` → `test_passed` or `bug_report` → fix `task_assignment` (retry) → … → `manager_report` / `clarification_request` — is **one** chain, retries included. |
| `causation_id` | `id` of the message whose handling sent this one; `null` for chain starters. Walking `causation_id` back gives the exact causal path. |

Rules:

- A message sent spontaneously (operator/message-api injection, heartbeat, replay relay, viewer rerun, agent_thinking) starts a new chain: `correlation_id == id`, `causation_id == null`.
- A handler replying to `msg` passes `**reply_ids(msg)`. The dev-team handlers (`app/agent_handlers/{coder,tester,manager,operator,common}.py`) do this for every send.
- Messages from older senders without `correlation_id` are tolerated: `correlation_of` falls back to their `id`, so a reply still links to them.
- The duet replay protocol keeps its own `airing_id` in payloads; its relay messages just get the default (own-id) correlation.
- `message-logger` persists both fields (`messages.correlation_id` / `causation_id`, indexed) — see `docs/database_schema.md` for the "follow one task" query. The feed pane can show a short tag (`content.correlation.show`, docs/message_bus_feed.md).

## Signature

```python
def load_worker_config(path: str) -> dict
def build_message(from_: str, to: str, type_: str, payload: dict | None = None,
                  correlation_id: str | None = None, causation_id: str | None = None) -> dict
def correlation_of(msg: dict | None) -> str | None
def reply_ids(msg: dict | None) -> dict   # {"correlation_id": ..., "causation_id": ...}

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
- `correlation_id` (str, optional, default None) — the chain this message belongs to. None starts a new chain: the message's own `id` is used.
- `causation_id` (str, optional, default None) — `id` of the message that directly caused this one; None for a chain starter.
- `msg` (`correlation_of` / `reply_ids`) — a received bus message dict; None, or a dict without `id`, is tolerated (new chain).
- `bootstrap_servers` (str, required) — Kafka bootstrap servers, e.g. `192.168.2.158:9092`.
- `topic` (str, required) — the single shared bus topic, e.g. `vtuber.messages`.
- `group_id` (str, required) — consumer group ID; must be unique per logical consumer (see `docs/VTuber_AI_Dev_Team_Concept.md` for the naming convention) so consumers don't steal each other's messages.
- `worker_id` (str, optional) — when set, `poll_new(to_filter=True)` only returns messages addressed to this worker or to `broadcast`.

## Return Value

- `load_worker_config` — parsed YAML as a `dict`.
- `build_message` — a `dict` with `id` (uuid4), `from`, `to`, `type`, `payload`, `timestamp` (ISO-8601 UTC), `correlation_id`, `causation_id` keys.
- `correlation_of` — `msg["correlation_id"]`, else `msg["id"]` (older senders), else None.
- `reply_ids` — `{"correlation_id": correlation_of(msg), "causation_id": msg["id"]}`, meant to be splatted into `build_message(..., **reply_ids(msg))`.
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
# Reply on the same chain as the message being handled
from message_bus import build_message, reply_ids

def handle(msg, producer):
    producer.send(build_message("tester", "manager", "test_passed",
                                {"task": msg["payload"]["task"]}, **reply_ids(msg)))
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
- v1.1.0 (2026-09-27) — Correlation IDs: `build_message` gains optional `correlation_id` / `causation_id` (new envelope keys; positional signature and existing keys unchanged), plus `correlation_of()` / `reply_ids()` helpers for handlers. Tests: `tests/test_correlation_ids.py`.
