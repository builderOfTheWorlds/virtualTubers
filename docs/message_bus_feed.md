# message_bus_feed (`app/tail_bus.py`)

## Overview

`tail_bus.py` is the standalone display process for the tmux **"Message Bus"**
pane. It consumes every message on the Kafka topic (not just those addressed to
the local worker) and prints a rich, colorized, aligned feed line per message
using plain `print()`. It is deliberately **not** a full-screen TUI so it renders
correctly under `xterm` + `ffmpeg` capture.

The module separates **pure** formatting/filtering helpers (unit-testable, no
Kafka) from the Kafka run loop (guarded under `if __name__ == "__main__"`), so
importing the module has no side effects.

Feed appearance and behavior are driven by a resolved `kafka_feed` panel config
(`--feed-config`). Colors live in the feed config only — never coupled to any
`avatar:` block — because senders include `broadcast`, which has no avatar.

## Signature

CLI entry point:

```
python3 /app/tail_bus.py --bus-config <worker.yaml> --feed-config <resolved kafka_feed panel yaml>
```

Key functions:

```python
def resolve(env_name: str, config_value, default=None)
def load_feed_config(path: str | None) -> dict
def passes_filters(msg: dict, filters: dict) -> bool
def format_timestamp(iso_ts: str, ts_config: dict) -> str
def format_payload(payload, payload_config: dict) -> str
def format_line(msg: dict, feed_config: dict) -> str
def format_header() -> str
def run(bus_config_path: str, feed_config_path: str | None) -> None
def main(argv: list[str] | None = None) -> None
```

## Parameters

### CLI flags

| Flag | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--bus-config` | path | no | `/config/worker.yaml` | worker.yaml providing Kafka connection + `worker_id` (has a `message_bus:` section). |
| `--feed-config` | path | no | `None` | Resolved `kafka_feed` panel yaml. Reads its `content:` sub-tree if present, else treats the whole file as the content block. If omitted, built-in defaults are used so the pane still runs. |

### Feed config (`content:`) schema

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `colors` | map | `{coder: green, manager: yellow, tester: magenta, broadcast: cyan, operator: blue}` | Sender color keyed by the message `from` value. |
| `filters.hide_types` | list | `[heartbeat, status_update]` | Types to drop. **status_update is the current heartbeat carrier** (see Notes). |
| `filters.show_types` | list | `[]` | Empty = show all except hidden; non-empty = whitelist. |
| `filters.direction` | str | `all` | `all` \| `broadcast` \| `to:<id>` \| `from:<id>`. |
| `highlight` | map | `{task_complete: green, clarification_request: yellow, error: red, bug_report: red, test_passed: green, manager_report: cyan, operator_reply: blue}` | Colors the TYPE column by category. |
| `payload.mode` | str | `pretty` | `pretty` \| `raw` \| `hidden`. |
| `payload.max_chars` | int | `80` | Truncation length (ellipsis appended). |
| `timestamp.format` | str | `%H:%M:%S` | `strftime` format. |
| `timestamp.local` | bool | `true` | Convert UTC ISO stamp to local time. |
| `header` | bool | `true` | Print a one-time column header at startup. |

### Environment overrides (env wins over file)

`KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`, `WORKER_ID` — resolved via `resolve()`.
`TAIL_BUS_LOG_LEVEL` — stderr log level (default `INFO`).

## Return Value

The pure helpers return strings/bools as documented. `run()` / `main()` run an
infinite display loop and do not return under normal operation.

## Rendering

Line format:

```
HH:MM:SS from ──▶ to  type  payload
```

- Sender colored per `colors` (keyed by `from`), padded to a fixed width.
- TYPE column colored per `highlight` when the type matches a category.
- Payload rendered per `payload.mode`, truncated to `payload.max_chars`.
- Timestamp converted to local time when `timestamp.local` is true.
- Filtering order: `hide_types` → `show_types` (whitelist if non-empty) → `direction`.

Colors are emitted as plain ANSI escape codes (no heavy deps). The xterm capture
is a real tty, so codes render; elsewhere they degrade to inert sequences.

## Dependencies

- Standard library: `os`, `sys`, `argparse`, `logging`, `datetime`.
- Third party: `PyYAML` (`yaml`).
- Internal: `message_bus` (`load_worker_config`, `MessageConsumer`, `BROADCAST`).

## Usage Examples

Run with the default worker config and built-in feed defaults:

```bash
python3 /app/tail_bus.py --bus-config /config/worker.yaml
```

Run with a resolved feed config emitted by the layout engine:

```bash
python3 /app/tail_bus.py \
  --bus-config /config/worker.yaml \
  --feed-config /tmp/panes/kafka_feed.yaml
```

## Logging

Structured logging goes to **stderr only** so the stdout feed stays clean:

- INFO on startup (resolved worker_id / bootstrap / topic).
- DEBUG on config load, filtered-out messages, run entry.
- ERROR inside the Kafka connect + poll/format try/except blocks; the loop stays
  alive on transient poll errors.

## Error Handling

- Missing/malformed `--feed-config` or `--bus-config` files are logged at ERROR
  and fall back to defaults / empty config rather than crashing.
- Bad timestamps return the raw string instead of raising.
- Kafka connect failure logs ERROR and re-raises (pane restart handled by tmux).
- Poll/format exceptions are caught per-iteration and logged; the feed continues.

## Notes

- **Heartbeat carrier:** `app/agent.py` publishes its per-tick flood as message
  type `status_update` (payload `{"text": "heartbeat #N"}`), **not** `heartbeat`.
  Both are hidden by default so the feed is not flooded. `agent.py` is not edited;
  filtering is fully configurable via `filters.hide_types`.

## Changelog

- **v1.0.0** (2026-07-01) — Rewrote the simple one-line consumer into a rich,
  configurable, filterable stdout feed with pure testable formatting/filtering
  helpers and a `--bus-config` / `--feed-config` CLI contract.
