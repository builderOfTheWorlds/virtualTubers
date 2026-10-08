#!/usr/bin/env python3
"""
agent.py
Agent loop: refreshes the worker's Redis liveness key every tick (plus a
rate-limited `status_update` bus heartbeat — agent.bus_heartbeat_every) and
dispatches every incoming message type (docs/VTuber_AI_Dev_Team_Concept.md
§3.4) via MESSAGE_HANDLERS.
Workers collaborate by role (agent config `role`): the coder replies
`task_complete` to the sender and hands the commit to the tester
(`commit_notification`), the tester "runs tests" and reports `test_passed`
or `bug_report` to the manager, and the manager re-delegates fixes (bounded
by MAX_BUG_RETRIES via `retry_count` traveling in message payloads) or
reports back to the operator (`manager_report`). Any role answers an
`operator_message` with an `operator_reply`.

This file is the entry point only (config, wiring, tick loop). The handlers
themselves live in app/agent_handlers/ (one module per role/concern — see
docs/agent_handlers.md); MESSAGE_HANDLERS is built there.
"""
import math
import time
import argparse

from message_bus import load_worker_config, build_message, correlation_of, resolve, MessageProducer, MessageConsumer
from worker_control import WorkerControl
from llm_client import build_llm_client
from coding_backend import build_coding_backend
from agent_state import resolve_state_path, write_state
from agent_metrics import AgentMetrics, InstrumentedLLMClient, MetricsProducerWrapper, resolve_runtime_dir
from agent_handlers import IDLE_TICK_HOOKS, MESSAGE_HANDLERS

# Backward-compatible re-exports: these names lived in this module before the
# agent_handlers/ split, and `from agent import ...` callers keep working.
# NOTE: re-exports are bindings, not aliases — monkeypatching `agent.<name>`
# does NOT affect the handlers; patch the agent_handlers module that looks
# the name up instead (see docs/agent_handlers.md).
from agent_handlers.common import _complete_with_emotion, _send_manager_report  # noqa: F401
from agent_handlers.relay_files import (  # noqa: F401
    DEFAULT_REPLAY_CUE_FILE,
    DEFAULT_REPLAY_READY_FILE,
    DEFAULT_REPLAY_REQUEST_FILE,
    DEFAULT_REPLAY_STOP_FILE,
    REPLAY_CUE_FILE_ENV,
    REPLAY_READY_FILE_ENV,
    REPLAY_REQUEST_FILE_ENV,
    REPLAY_STOP_FILE_ENV,
    _atomic_write_json,
    _read_json_file,
    _resolve_replay_cue_file,
    _resolve_replay_ready_file,
    _resolve_replay_request_file,
    _resolve_replay_stop_file,
    _write_replay_request,
)
from agent_handlers.coder import (  # noqa: F401
    demo_editor_note,
    demo_filetree_ls,
    handle_task_assignment,
    show_commit_in_filetree,
)
from agent_handlers.tester import (  # noqa: F401
    BUG_SEVERITIES,
    BUG_SEVERITY_WEIGHTS,
    TEST_PASS_PROBABILITY,
    WORKSPACE_MOUNT_PATTERN,
    _decide_test_outcome,
    _resolve_workspace,
    _run_tests_and_report,
    _severity_from_failures,
    handle_commit_notification,
    handle_retest_request,
)
from agent_handlers.manager import (  # noqa: F401
    MAX_BUG_RETRIES,
    handle_bug_report,
    handle_clarification_request,
    handle_task_complete,
    handle_test_passed,
)
from agent_handlers.operator import handle_operator_message  # noqa: F401
from agent_handlers.viewer import _pick_rerun_episode, handle_viewer_joined  # noqa: F401
from agent_handlers.replay_relay import (  # noqa: F401
    _is_valid_cast,
    handle_replay_cue,
    handle_replay_end,
    handle_replay_invite,
    handle_replay_ready,
    handle_replay_request,
    handle_replay_stop,
)


# Bus heartbeat cadence default: one status_update every 12 ticks (~1 min at
# the default 5 s tick). Liveness lives in Redis (worker:{id}:alive) now; the
# low-rate bus beat is kept only so the operator's "include status_update"
# log-filter toggle still has something to record. 0 turns it off.
DEFAULT_BUS_HEARTBEAT_EVERY = 12
# Liveness key TTL when agent.liveness_ttl_s is unset: this many ticks, with a
# floor, so one slow tick (an LLM call) doesn't flap the worker to "dead".
LIVENESS_TTL_TICKS = 3
MIN_LIVENESS_TTL_S = 15


def resolve_bus_heartbeat_every(agent_config):
    """agent.bus_heartbeat_every as a non-negative int (0 = off). A missing
    or malformed value falls back to DEFAULT_BUS_HEARTBEAT_EVERY."""
    value = (agent_config or {}).get("bus_heartbeat_every", DEFAULT_BUS_HEARTBEAT_EVERY)
    if value is None:
        return DEFAULT_BUS_HEARTBEAT_EVERY
    try:
        value = int(value)
    except (TypeError, ValueError):
        print(f"[agent] WARN event=bad_config key=agent.bus_heartbeat_every value={value!r} "
              f"fallback={DEFAULT_BUS_HEARTBEAT_EVERY}")
        return DEFAULT_BUS_HEARTBEAT_EVERY
    return max(0, value)


def resolve_liveness_ttl(agent_config, tick_rate_s):
    """agent.liveness_ttl_s if set (> 0), else max(LIVENESS_TTL_TICKS ticks,
    MIN_LIVENESS_TTL_S) seconds, rounded up to whole seconds."""
    configured = (agent_config or {}).get("liveness_ttl_s")
    if configured is not None:
        try:
            if int(configured) > 0:
                return int(configured)
        except (TypeError, ValueError):
            print(f"[agent] WARN event=bad_config key=agent.liveness_ttl_s value={configured!r} fallback=auto")
    return max(math.ceil(tick_rate_s * LIVENESS_TTL_TICKS), MIN_LIVENESS_TTL_S)


def bus_heartbeat_due(tick, every):
    """True on the ticks that should also publish a status_update bus
    heartbeat: tick 0 and every `every` ticks after; never when every <= 0."""
    return every > 0 and tick % every == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/config/worker.yaml")
    args = parser.parse_args()

    config = load_worker_config(args.config)
    agent_config = config.get("agent", {})
    bus_config = config.get("message_bus", {})

    worker_id = resolve("WORKER_ID", bus_config.get("worker_id"), "worker")
    bootstrap_servers = resolve("KAFKA_BOOTSTRAP_SERVERS", bus_config.get("bootstrap_servers"), "localhost:9092")
    topic = resolve("KAFKA_TOPIC", bus_config.get("topic"))
    tick_rate_s = agent_config.get("tick_rate_ms", 5000) / 1000
    bus_heartbeat_every = resolve_bus_heartbeat_every(agent_config)
    liveness_ttl_s = resolve_liveness_ttl(agent_config, tick_rate_s)

    llm_client = build_llm_client(config)
    state_path = resolve_state_path(agent_config)

    # A broken coding-backend setup (missing tool, unwritable volume) must
    # not take the worker down — it degrades to narration-only and the
    # operator sees why in the logs.
    try:
        coding_backend = build_coding_backend(config, llm_client)
    except Exception as exc:
        print(f"[agent] WARN coding backend unavailable, running narration-only: {exc}")
        coding_backend = None

    print(f"[agent] {worker_id} started. Config: {args.config}")
    print(f"[agent] Kafka bootstrap={bootstrap_servers} topic={topic}")
    print(f"[agent] LLM provider={config.get('llm', {}).get('provider', 'ollama')}")
    print(f"[agent] coding backend={coding_backend.name if coding_backend else 'none'}")
    print(f"[agent] avatar state file={state_path}")
    print(f"[agent] liveness key=worker:{worker_id}:alive ttl_s={liveness_ttl_s} "
          f"bus_heartbeat_every={bus_heartbeat_every}")

    write_state(state_path, "idle", action="starting up")

    producer = MessageProducer(bootstrap_servers, topic)
    consumer = MessageConsumer(bootstrap_servers, topic, group_id=f"vtuber-agent-{worker_id}", worker_id=worker_id)
    control = WorkerControl.from_config(config)

    # Instrumentation choke point (docs/tuber_base_layout_plan.md, Frozen
    # Contract A/B): wrap the already-built llm_client + producer so all 8
    # existing llm_client.complete(...) call sites downstream get thinking
    # narration, metrics tracking, and messages_sent counting for free,
    # with no per-call-site changes. AgentMetrics is shared between the two
    # wrappers so producer.send(...) calls made by message handlers AND the
    # instrumented client's own agent_thinking publish both count toward
    # messages_sent.
    metrics_runtime_dir = resolve_runtime_dir()
    shared_metrics = AgentMetrics(worker_id, runtime_dir=metrics_runtime_dir)
    producer = MetricsProducerWrapper(producer, shared_metrics)
    llm_client = InstrumentedLLMClient(llm_client, producer, worker_id, metrics=shared_metrics,
                                       publish_thinking=agent_config.get("publish_thinking", True))

    i = 0
    while True:
        # Liveness first, and even while disabled: "alive" means the process
        # is ticking; "enabled" is the separate operator toggle. Never raises.
        control.heartbeat(worker_id, liveness_ttl_s)
        if not control.is_enabled(worker_id):
            write_state(state_path, "idle", action="disabled by operator")
            time.sleep(tick_rate_s)
            continue

        tick_ok = True
        try:
            for msg in consumer.poll_new():
                print(f"[agent:{worker_id}] received {msg['type']} from {msg['from']} "
                      f"correlation_id={correlation_of(msg)}: {msg['payload']}")
                handler = MESSAGE_HANDLERS.get(msg["type"])
                if handler:
                    handler(worker_id, agent_config, llm_client, producer, msg, state_path,
                            coding_backend=coding_backend)

            # Optional per-role hook (e.g. the manager's task backlog,
            # docs/task_backlog.md). Enabled ticks only, like the handlers.
            idle_hook = IDLE_TICK_HOOKS.get(agent_config.get("role"))
            if idle_hook:
                idle_hook(worker_id, agent_config, llm_client, producer, state_path)

            if bus_heartbeat_due(i, bus_heartbeat_every):
                heartbeat = build_message(worker_id, "broadcast", "status_update", {"text": f"heartbeat #{i}"})
                producer.send(heartbeat)
                print(f"[agent:{worker_id}] {heartbeat['type']} #{i}")
        except Exception as exc:
            tick_ok = False
            print(f"[agent:{worker_id}] ERROR unhandled exception in tick #{i}: {exc}")
            raise
        finally:
            llm_client.metrics.record_tick(tick_ok)

        i += 1
        time.sleep(tick_rate_s)


if __name__ == "__main__":
    main()
