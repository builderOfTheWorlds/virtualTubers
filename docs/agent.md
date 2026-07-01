# agent.py

## Overview

The worker's agent loop: `perceive()` (poll the Kafka bus for messages
addressed to this worker), `think()` (call the configured LLM with the
worker's system prompt when a `task_assignment` arrives), and `act()`
(reply on the bus with `task_complete` and a narration, or
`clarification_request` on failure). Every tick also publishes a
`status_update` heartbeat, unchanged from the earlier stub.

On each task-handling lifecycle event (`thinking` before the LLM call,
`speaking`/`frustrated` with the narration/error after it), `act()` also
writes to the small local state file `agent_state.py` owns — this is what
lets the avatar pane (`avatar.py`) show the right expression and speech
bubble instead of auto-cycling on a blind timer. See `docs/agent_state.md`.

On `task_assignment`, `act()` also calls two scripted (non-LLM)
`tmux_control.py`-driven demo actions — a first, deliberately simple
exercise of "the agent acting on its own tmux UI" ahead of any real
LLM-driven tool use (see `docs/tmux_control.md`):
- `demo_editor_note` focuses the editor pane and types a fixed
  `# TODO: <task>` comment.
- `demo_filetree_ls` focuses the filetree pane (an interactive shell as of
  `config/panels/filetree.yaml`'s `bash -c "tree ...; exec bash"` — no
  longer a `watch` loop, which can't accept keystrokes as commands) and runs
  `ls`, then refocuses the editor pane.

This is the "think + narrate" slice of the agent brain — it proves the
instruction round trip (operator/manager → worker → LLM → reply on stream)
end to end, now visibly landing on the avatar and editor panes as well as
the Kafka feed pane. The LLM itself still can't choose what to type or
which pane to touch — `llm_client.complete()` returns free-form narration
text only, no structured tool calls — see `docs/VTuber_AI_Dev_Team_Concept.md`
Phase 1 roadmap for what's next.

## Signature

```python
def resolve(env_name: str, config_value, default=None)

def demo_editor_note(worker_id: str, task: str) -> None

def demo_filetree_ls(worker_id: str) -> None

def handle_task_assignment(worker_id: str, agent_config: dict, llm_client, producer: MessageProducer, msg: dict, state_path: str | None = None) -> None

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
- `state_path` (str | None) — where to write avatar state
  (`agent_state.write_state`); `None` skips the write (used by tests that
  don't care about the avatar side effect).
- `task` (str, `demo_editor_note`) — the task description; flattened to one
  line and typed as a comment.
- `--config` (CLI flag, default `/config/worker.yaml`) — path to the worker's
  YAML config.

## Return Value

- `handle_task_assignment` / `demo_editor_note` / `demo_filetree_ls` —
  `None`; side effects only (Kafka publish + console `print`; tmux pane
  focus/keystrokes).
- `main` — never returns; runs the tick loop until the process is killed.

## Dependencies

- `message_bus` (`load_worker_config`, `build_message`, `MessageProducer`, `MessageConsumer`)
- `llm_client` (`build_llm_client`)
- `agent_state` (`resolve_state_path`, `write_state`)
- `tmux_control` (`select_pane`, `send_keys`, `send_raw`, `send_command`, `TmuxError`)
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
- `demo_editor_note` and `demo_filetree_ls` each catch `TmuxError`/`OSError`
  around every tmux call (pane not found, no tmux session yet, or the
  `tmux` binary missing entirely — e.g. running `agent.py` outside the
  container) and just log it — these are cosmetic demo actions, so neither
  may ever take the tick loop down.

## Changelog

- v1.4.0 (2026-07-01) — Added `demo_filetree_ls`, called alongside
  `demo_editor_note` on every `task_assignment`: focuses the filetree pane
  and runs `ls`, then refocuses the editor. Required changing
  `config/panels/filetree.yaml` from a `watch -n2 tree` loop (which can't
  accept keystrokes as commands) to an interactive shell.
- v1.3.0 (2026-07-01) — Added `demo_editor_note`, called from
  `handle_task_assignment` on every `task_assignment`: a scripted
  `tmux_control.py`-driven action that focuses the editor pane and types a
  fixed TODO comment noting the task.
- v1.2.0 (2026-07-01) — Wired `handle_task_assignment` and `main` to write
  avatar state (`agent_state.write_state`) on `thinking`/`speaking`/
  `frustrated` transitions, so `avatar.py` reflects live agent activity
  instead of auto-cycling blind.
- v1.1.0 (2026-07-01) — Replaced the heartbeat-only stub with a real
  perceive/think/act loop: `task_assignment` messages now trigger an LLM
  call (via the new `llm_client.py`, provider-switchable between Ollama and
  Claude) and a `task_complete`/`clarification_request` reply.
- v1.0.0 — Initial stub: heartbeat `status_update` per tick, printed any
  message addressed to the worker.
