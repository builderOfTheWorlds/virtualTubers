# replay_pane

## Overview

Long-lived tmux pane program for **Rerun Theater** — the stream feature
that re-performs past real dev sessions as shows. It idles with an episode
listing and performs an episode (via [replay.md](replay.md)) whenever the
agent drops a request file.

The full wiring, operator to screen:

```
operator ──POST /messages──▶ Kafka ──▶ agent.py handle_replay_request
                                            │ writes REPLAY_REQUEST_FILE (atomic)
                                            ▼
                              replay_pane.py (this program, polling)
                                            │ resolves episode INSIDE library
                                            ▼
                              Performer renders the show + avatar reacts
```

File-based handoff on purpose (same pattern as `agent_state.py`): the pane
never consumes Kafka and never executes anything from the bus. The only
thing a bus message can influence is **which pre-built, pre-redacted
episode in the library plays** — episode names are resolved basename-only
inside `REPLAY_LIBRARY`, so a hostile payload cannot reach other files.

## Signature

```python
def resolve_episode(library, episode) -> Path | None
def read_request(request_file) -> dict | None      # consume-once
def perform_request(request, library, worker_name, state_path,
                    default_speed=1.0) -> bool
def list_episodes(library) -> list[str]
```

## Parameters (CLI / environment)

- `--library` / `REPLAY_LIBRARY` (default `/data/replays`): episode script
  directory — mounted `:ro` from `/opt/virtualTubers/replays` in
  `docker-compose.yml`.
- `--request-file` / `REPLAY_REQUEST_FILE` (default
  `/tmp/replay_request.json`): the agent → pane handoff file. Same value
  must be visible to `agent.py` (same container, both default it).
- `--worker-name` / `WORKER_ID` (default `worker`): persona name on
  dialogue lines when the request doesn't override it.
- `--once`: handle at most one pending request then exit (testing).

## Return Value

Runs forever (pane lifetime). Malformed requests are consumed and logged —
never a crash loop. A failed episode logs to stderr and returns to idle.

## Dependencies

`app/replay.py` (Performer), `app/agent_state.py` (avatar state path),
standard library.

## Usage Examples

Operator: switch a worker into rerun mode (config-only), then request a show:

```bash
# 1. worker config: layout.preset: replay   (or LAYOUT_PRESET=replay env)
# 2. queue an episode on the running worker:
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "2026-07-02_04-27-00_6ecdde82", "speed": 1.5}}'
```

Build and ship the episode library (from the machine with the logs):

```bash
.venv/Scripts/python.exe scripts/build_replay_library.py \
  --logs "C:/Users/<you>/.../claudeBackupUtility/logs/claude/virtualTubers" \
  --out replays
# then sync replays/ to the host: /opt/virtualTubers/replays
```

## Error Handling

- Unknown episode → stderr report + `False`; pane returns to idle. The
  agent already confirmed queueing to the operator; check worker logs.
- Malformed request file → consumed and discarded (logged).
- Missing library dir → idle screen says so; nothing crashes.
- Avatar state write failures are non-fatal (see replay.md).

## Changelog

- **v1.0.0** (2026-07-12): Initial version — idle screen with episode
  listing, request-file polling, traversal-safe episode resolution,
  `--once` test mode. Wired to `agent.py` `replay_request` +
  `config/panels/replay.yaml` + `config/layouts/replay.yaml`.
