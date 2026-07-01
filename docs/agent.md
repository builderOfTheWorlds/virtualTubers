# agent.py

## Overview

The worker's agent loop: `perceive()` (poll the Kafka bus for messages
addressed to this worker), `think()` (call the configured LLM with the
worker's system prompt when a `task_assignment` arrives), and `act()`
(reply on the bus with `task_complete` and a narration, or
`clarification_request` on failure). Every tick also publishes a
`status_update` heartbeat, unchanged from the earlier stub.

This is the "think + narrate" slice of the agent brain — it proves the
instruction round trip (operator/manager → worker → LLM → reply on stream)
end to end. It does not yet write files, run commands, or otherwise act on
the shared repo; see `docs/VTuber_AI_Dev_Team_Concept.md` Phase 1 roadmap for
what's next.

## Signature

```python
def resolve(env_name: str, config_value, default=None)

def handle_task_assignment(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict) -> None

def main() -> None
```

## Parameters

- `env_name` / `config_value` / `default` — `resolve` picks an environment
  variable over a config value over a default, used for `WORKER_ID`,
  `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`.
- `worker_id` (str) — this worker's ID, used as `from` on outgoing messages.
- `agent_config` (dict) — `config["agent"]`; only `system_prompt` is read here.
- `llm_client` — an `OllamaClient`/`ClaudeClient` from `llm_client.build_llm_client`.
- `producer` (`MessageProducer`) — used to publish the reply.
- `msg` (dict) — the received message envelope; `msg["payload"]["task"]` is
  the task description, `msg["from"]` is who to reply to.
- `--config` (CLI flag, default `/config/worker.yaml`) — path to the worker's
  YAML config.

## Return Value

- `handle_task_assignment` — `None`; side effect is a Kafka publish (and a
  console `print` for the tmux "agent chat" / message-bus feed pane to show).
- `main` — never returns; runs the tick loop until the process is killed.

## Dependencies

- `message_bus` (`load_worker_config`, `build_message`, `MessageProducer`, `MessageConsumer`)
- `llm_client` (`build_llm_client`)
- Python standard library: `os`, `time`, `argparse`

## Usage Examples

```bash
python3 app/agent.py --config config/workers/coder.yaml
```

```bash
# Inject a task for the coder worker to narrate (see docs/message_api.md)
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "task_assignment", "payload": {"task": "fix the login bug"}}'
```

The coder's console output and Kafka feed pane show the LLM's in-character
narration, followed by a `task_complete` message back to whoever sent the
task (`from` field on the original message, `"operator"` when sent via
`message-api`).

## Error Handling

- If the LLM call in `handle_task_assignment` raises (network error, bad API
  key, rate limit, etc.), the exception is caught, logged, and a
  `clarification_request` message is published with the error text instead
  of crashing the tick loop — one bad LLM call doesn't take the worker off
  stream.
- Malformed/missing config (`load_worker_config`), an unreachable Kafka
  broker (`MessageProducer`/`MessageConsumer` construction), or an unknown
  `llm.provider` (`build_llm_client`) are all fatal at startup and left
  uncaught, matching `message_bus.py`'s fail-fast convention — Docker's
  `restart: unless-stopped` handles the retry.

## Changelog

- v1.1.0 (2026-07-01) — Replaced the heartbeat-only stub with a real
  perceive/think/act loop: `task_assignment` messages now trigger an LLM
  call (via the new `llm_client.py`, provider-switchable between Ollama and
  Claude) and a `task_complete`/`clarification_request` reply.
- v1.0.0 — Initial stub: heartbeat `status_update` per tick, printed any
  message addressed to the worker.
