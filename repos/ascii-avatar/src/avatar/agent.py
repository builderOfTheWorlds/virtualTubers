"""Avatar agent — intelligent control loop using Haiku for decisions."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable

import anthropic
import zmq

from avatar.agent_prompt import build_prompt, parse_response
from avatar.session_tracker import SessionTracker

log = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
BATCH_INTERVAL = 3.0  # seconds
DEBOUNCE_INTERVAL = 10.0  # seconds — minimum gap between speech
STALE_THRESHOLD = 30.0  # seconds before a session is marked idle

# Visual state priority (highest first)
STATE_PRIORITY = {"error": 4, "thinking": 3, "speaking": 2, "listening": 1, "idle": 0}

# Hook event → visual state mapping
HOOK_STATE_MAP = {
    "PreToolUse": "thinking",
    "PostToolUse": "thinking",
    "PostToolUseFailure": "error",
    "UserPromptSubmit": "listening",
    "Stop": "idle",
}


class AgentLoop:
    """Core agent decision engine.

    Args:
        socket_path: ZeroMQ IPC socket path for receiving hook events.
        dry_run: If True, skip ZeroMQ binding and Anthropic client init (for testing).
    """

    def __init__(self, socket_path: str, dry_run: bool = False) -> None:
        self._socket_path = socket_path
        self._dry_run = dry_run
        self._tracker = SessionTracker()
        self._client: anthropic.Anthropic | None = None
        self._last_speech_time: float | None = None
        self._events_this_cycle: int = 0
        self._cycle_lock = threading.Lock()
        self._stop_event = threading.Event()
        self.on_state_change: Callable[[str], None] | None = None
        self.on_speak: Callable[[str], None] | None = None
        self._zmq_context: zmq.Context | None = None
        self._zmq_socket: zmq.Socket | None = None
        self._listener_thread: threading.Thread | None = None

        if not dry_run:
            self._client = anthropic.Anthropic()

    def _process_raw_event(self, data: dict[str, Any]) -> None:
        """Process a raw hook event from ZeroMQ."""
        hook = data.get("hook")
        if not hook:
            return  # Ignore legacy events without "hook" field

        session_id = data.get("session_id", "unknown")
        cwd = data.get("cwd", "/unknown")

        with self._cycle_lock:
            self._tracker.update(session_id, cwd, hook)
            self._events_this_cycle += 1

    def _hook_to_state(self, hook: str) -> str:
        """Map a hook event type to a visual state."""
        return HOOK_STATE_MAP.get(hook, "idle")

    def _highest_priority_state(self, states: list[str]) -> str:
        """Return the highest-priority state from a list."""
        if not states:
            return "idle"
        return max(states, key=lambda s: STATE_PRIORITY.get(s, 0))

    def _should_suppress_speech(self) -> bool:
        """Return True if speech should be suppressed due to debounce."""
        if self._last_speech_time is None:
            return False
        elapsed = time.monotonic() - self._last_speech_time
        return elapsed < DEBOUNCE_INTERVAL

    def _decide(self) -> tuple[str, str | None]:
        """Run one decision cycle. Returns (state, speak_text_or_none)."""
        # Snapshot and reset cycle state under lock to avoid races with listener thread
        with self._cycle_lock:
            self._tracker.mark_stale(threshold=STALE_THRESHOLD)
            events_count = self._events_this_cycle
            summary = self._tracker.summarize() if events_count > 0 else []
            self._events_this_cycle = 0
            self._tracker.reset_counts()

        if events_count == 0:
            return ("idle", None)

        # Calculate time since last speech for debounce context
        last_speech_ago = None
        if self._last_speech_time is not None:
            last_speech_ago = time.monotonic() - self._last_speech_time

        prompt = build_prompt(sessions=summary, last_speech_ago=last_speech_ago)

        # Call Haiku
        if self._client is None:
            # dry_run or no client — use visual state heuristic only
            states = [self._hook_to_state(s["last_event"]) for s in summary]
            return (self._highest_priority_state(states), None)

        try:
            response = self._client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=100,
                system=prompt["system"],
                messages=[{"role": "user", "content": prompt["user"]}],
            )
            if not response.content:
                log.warning("Haiku returned empty content")
                raw = ""
            else:
                raw = response.content[0].text
            state, speak = parse_response(raw)
        except Exception as e:
            log.error("Haiku call failed: %s", e)
            states = [self._hook_to_state(s["last_event"]) for s in summary]
            state = self._highest_priority_state(states)
            speak = None

        # Apply debounce
        if speak and self._should_suppress_speech():
            has_error = any(s["error_count"] > 0 for s in summary)
            if not has_error:
                speak = None

        if speak:
            self._last_speech_time = time.monotonic()

        return (state, speak)

    def start_listener(self) -> None:
        """Start the ZeroMQ listener thread."""
        self._stop_event.clear()
        self._zmq_context = zmq.Context()
        self._zmq_socket = self._zmq_context.socket(zmq.PULL)
        self._zmq_socket.bind(f"ipc://{self._socket_path}")
        self._listener_thread = threading.Thread(
            target=self._listen_loop, daemon=True,
        )
        self._listener_thread.start()

    def _listen_loop(self) -> None:
        """Background thread: receive events from ZeroMQ and buffer them."""
        assert self._zmq_socket is not None
        poller = zmq.Poller()
        poller.register(self._zmq_socket, zmq.POLLIN)

        while not self._stop_event.is_set():
            socks = dict(poller.poll(timeout=100))
            if self._zmq_socket in socks:
                try:
                    raw = self._zmq_socket.recv(zmq.NOBLOCK)
                    data = json.loads(raw)
                    self._process_raw_event(data)
                except (json.JSONDecodeError, ValueError) as e:
                    log.warning("Malformed event: %s", e)

    def stop_listener(self) -> None:
        """Stop the ZeroMQ listener and clean up."""
        self._stop_event.set()
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=2)
        if self._zmq_socket is not None:
            self._zmq_socket.close()
        if self._zmq_context is not None:
            self._zmq_context.term()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

    def _act(self, state: str, speak: str | None) -> None:
        """Execute the agent's decision: update state and optionally speak."""
        if self.on_state_change:
            self.on_state_change(state)
        if speak and self.on_speak:
            self.on_speak(speak)

    def run(self) -> None:
        """Main agent loop: collect → decide → act, on BATCH_INTERVAL cycle."""
        self.start_listener()
        log.info("Agent loop started. Socket: %s", self._socket_path)
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=BATCH_INTERVAL)
                if self._stop_event.is_set():
                    break
                state, speak = self._decide()
                self._act(state, speak)
        finally:
            self.stop_listener()

    def stop(self) -> None:
        """Signal the agent loop to stop."""
        self._stop_event.set()
