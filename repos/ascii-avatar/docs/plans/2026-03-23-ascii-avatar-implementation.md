# ASCII Avatar Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a terminal-based ASCII avatar companion for Claude Code that animates in response to activity and speaks via local TTS.

**Architecture:** Separate Python process communicating with Claude Code via ZeroMQ PUSH/PULL over Unix socket. Renderer (blessed) displays cyberpunk ASCII frames driven by a state machine. Pluggable TTS with Kokoro as primary engine. Persona system bundles theme + voice + personality.

**Tech Stack:** Python 3.12, blessed, pyzmq, kokoro-onnx, sounddevice, numpy, mcp SDK

---

### Task 1: Project Scaffold & pyproject.toml

**Files:**
- Create: `src/avatar/__init__.py`
- Create: `src/avatar/voice/__init__.py`
- Create: `src/avatar/bridge/__init__.py`
- Create: `src/avatar/frames/__init__.py`
- Create: `tests/__init__.py`
- Create: `pyproject.toml`
- Create: `assets/frames/.gitkeep`

**Step 1: Create directory structure**

```bash
cd /path/to/ascii-avatar
mkdir -p src/avatar/voice src/avatar/bridge src/avatar/frames tests assets/frames scripts
```

**Step 2: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "ascii-avatar"
version = "0.1.0"
description = "Terminal ASCII avatar companion for Claude Code"
requires-python = ">=3.11"
license = "MIT"
dependencies = [
    "blessed",
    "pyzmq",
    "kokoro-onnx",
    "sounddevice",
    "numpy",
    "mcp",
]

[project.optional-dependencies]
elevenlabs = ["elevenlabs"]
piper = ["piper-tts"]
dev = ["pytest", "pytest-timeout"]

[project.scripts]
avatar = "avatar.main:main"

[tool.hatch.build.targets.wheel]
packages = ["src/avatar"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

**Step 3: Create `__init__.py` files**

`src/avatar/__init__.py`:
```python
"""ASCII Avatar companion for Claude Code."""
```

`src/avatar/voice/__init__.py`:
```python
"""Voice synthesis engines."""
```

`src/avatar/bridge/__init__.py`:
```python
"""Claude Code integration bridge."""
```

`src/avatar/frames/__init__.py`:
```python
"""ASCII art frame sets."""
```

`tests/__init__.py`: empty file

`assets/frames/.gitkeep`: empty file

**Step 4: Create venv and install in dev mode**

```bash
cd /path/to/ascii-avatar
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Note: `kokoro-onnx` and `mcp` may fail to install at this stage if system deps are missing. That's fine — we'll build the core components first (state machine, renderer, event bus) which only need `blessed`, `pyzmq`, `numpy`. If install partially fails, install just the core deps:

```bash
uv pip install blessed pyzmq numpy pytest pytest-timeout
uv pip install -e . --no-deps
```

**Step 5: Verify pytest runs**

```bash
cd /path/to/ascii-avatar
python -m pytest tests/ -v
```

Expected: `no tests ran` (0 collected, no errors)

**Step 6: Commit**

```bash
git add -A
git commit -m "scaffold: project structure and pyproject.toml"
```

---

### Task 2: State Machine (TDD)

**Files:**
- Create: `src/avatar/state_machine.py`
- Create: `tests/test_state_machine.py`

**Step 1: Write failing tests**

`tests/test_state_machine.py`:
```python
import threading
import time

import pytest

from avatar.state_machine import AvatarState, AvatarStateMachine


class TestAvatarState:
    def test_states_exist(self):
        assert AvatarState.IDLE is not None
        assert AvatarState.THINKING is not None
        assert AvatarState.SPEAKING is not None
        assert AvatarState.LISTENING is not None
        assert AvatarState.ERROR is not None


class TestStateMachine:
    def test_initial_state_is_idle(self):
        sm = AvatarStateMachine()
        assert sm.state == AvatarState.IDLE

    def test_transition_to_thinking(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.THINKING)
        assert sm.state == AvatarState.THINKING

    def test_transition_to_speaking(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.SPEAKING)
        assert sm.state == AvatarState.SPEAKING

    def test_transition_to_listening(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.LISTENING)
        assert sm.state == AvatarState.LISTENING

    def test_transition_to_error(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.ERROR)
        assert sm.state == AvatarState.ERROR

    def test_transition_back_to_idle(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.THINKING)
        sm.transition(AvatarState.IDLE)
        assert sm.state == AvatarState.IDLE

    def test_entry_exit_callbacks(self):
        log = []
        sm = AvatarStateMachine(
            on_enter=lambda s: log.append(("enter", s)),
            on_exit=lambda s: log.append(("exit", s)),
        )
        sm.transition(AvatarState.THINKING)
        assert log == [("exit", AvatarState.IDLE), ("enter", AvatarState.THINKING)]

    def test_no_callback_on_same_state(self):
        log = []
        sm = AvatarStateMachine(
            on_enter=lambda s: log.append(("enter", s)),
            on_exit=lambda s: log.append(("exit", s)),
        )
        sm.transition(AvatarState.IDLE)
        assert log == []

    def test_thread_safety(self):
        sm = AvatarStateMachine()
        results = []

        def rapid_transitions(target_state, count):
            for _ in range(count):
                sm.transition(target_state)
                results.append(sm.state)

        t1 = threading.Thread(target=rapid_transitions, args=(AvatarState.THINKING, 100))
        t2 = threading.Thread(target=rapid_transitions, args=(AvatarState.SPEAKING, 100))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # All results should be valid states (no corruption)
        for r in results:
            assert r in AvatarState

    @pytest.mark.timeout(5)
    def test_auto_idle_timeout(self):
        sm = AvatarStateMachine(idle_timeout=0.3)
        sm.transition(AvatarState.THINKING)
        assert sm.state == AvatarState.THINKING
        time.sleep(0.5)
        assert sm.state == AvatarState.IDLE

    @pytest.mark.timeout(5)
    def test_auto_idle_resets_on_new_transition(self):
        sm = AvatarStateMachine(idle_timeout=0.5)
        sm.transition(AvatarState.THINKING)
        time.sleep(0.2)
        sm.transition(AvatarState.SPEAKING)
        time.sleep(0.2)
        # Should still be speaking — timeout reset when we transitioned
        assert sm.state == AvatarState.SPEAKING

    def test_speaking_with_phoneme_data(self):
        sm = AvatarStateMachine()
        phonemes = [
            {"phoneme": "h", "start": 0.0, "end": 0.1},
            {"phoneme": "ɛ", "start": 0.1, "end": 0.2},
        ]
        sm.transition(AvatarState.SPEAKING, phoneme_data=phonemes)
        assert sm.state == AvatarState.SPEAKING
        assert sm.phoneme_data == phonemes

    def test_phoneme_data_cleared_on_exit_speaking(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.SPEAKING, phoneme_data=[{"phoneme": "a"}])
        sm.transition(AvatarState.IDLE)
        assert sm.phoneme_data == []

    def test_shutdown(self):
        sm = AvatarStateMachine(idle_timeout=1.0)
        sm.transition(AvatarState.THINKING)
        sm.shutdown()
        # After shutdown, idle timer thread should be stopped
        assert sm._shutdown_event.is_set()
```

**Step 2: Run tests to verify they fail**

```bash
cd /path/to/ascii-avatar
python -m pytest tests/test_state_machine.py -v
```

Expected: ImportError — `avatar.state_machine` does not exist

**Step 3: Implement state machine**

`src/avatar/state_machine.py`:
```python
"""Avatar state management with thread-safe transitions."""

from __future__ import annotations

import enum
import threading
from typing import Any, Callable


class AvatarState(enum.Enum):
    IDLE = "idle"
    THINKING = "thinking"
    SPEAKING = "speaking"
    LISTENING = "listening"
    ERROR = "error"


class AvatarStateMachine:
    """Thread-safe state machine for the avatar.

    Args:
        on_enter: Callback fired when entering a new state.
        on_exit: Callback fired when leaving a state.
        idle_timeout: Seconds before auto-returning to IDLE. 0 = disabled.
    """

    def __init__(
        self,
        on_enter: Callable[[AvatarState], None] | None = None,
        on_exit: Callable[[AvatarState], None] | None = None,
        idle_timeout: float = 0,
    ) -> None:
        self._state = AvatarState.IDLE
        self._lock = threading.Lock()
        self._on_enter = on_enter
        self._on_exit = on_exit
        self._idle_timeout = idle_timeout
        self._phoneme_data: list[dict[str, Any]] = []
        self._shutdown_event = threading.Event()
        self._idle_timer: threading.Timer | None = None

    @property
    def state(self) -> AvatarState:
        with self._lock:
            return self._state

    @property
    def phoneme_data(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._phoneme_data)

    def transition(
        self,
        new_state: AvatarState,
        phoneme_data: list[dict[str, Any]] | None = None,
    ) -> None:
        with self._lock:
            if self._shutdown_event.is_set():
                return
            if new_state == self._state:
                return

            old_state = self._state

            # Cancel pending idle timer
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None

            # Clear phoneme data when leaving SPEAKING
            if old_state == AvatarState.SPEAKING:
                self._phoneme_data = []

            if self._on_exit:
                self._on_exit(old_state)

            self._state = new_state

            # Store phoneme data for SPEAKING
            if new_state == AvatarState.SPEAKING and phoneme_data:
                self._phoneme_data = list(phoneme_data)

            if self._on_enter:
                self._on_enter(new_state)

            # Schedule auto-idle if timeout enabled and not already idle
            if (
                self._idle_timeout > 0
                and new_state != AvatarState.IDLE
                and not self._shutdown_event.is_set()
            ):
                self._idle_timer = threading.Timer(
                    self._idle_timeout, self._auto_idle
                )
                self._idle_timer.daemon = True
                self._idle_timer.start()

    def _auto_idle(self) -> None:
        if not self._shutdown_event.is_set():
            self.transition(AvatarState.IDLE)

    def shutdown(self) -> None:
        self._shutdown_event.set()
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_state_machine.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/avatar/state_machine.py tests/test_state_machine.py
git commit -m "feat: state machine with thread-safe transitions and auto-idle"
```

---

### Task 3: Event Bus (TDD)

**Files:**
- Create: `src/avatar/event_bus.py`
- Create: `tests/test_event_bus.py`

**Step 1: Write failing tests**

`tests/test_event_bus.py`:
```python
import json
import os
import tempfile
import threading
import time

import pytest
import zmq

from avatar.event_bus import AvatarEvent, EventBus


@pytest.fixture
def socket_path(tmp_path):
    return str(tmp_path / "test-avatar.sock")


@pytest.fixture
def event_bus(socket_path):
    bus = EventBus(socket_path=socket_path)
    yield bus
    bus.stop()


def send_event(socket_path: str, event: dict) -> None:
    """Helper: send a single event via PUSH socket."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.connect(f"ipc://{socket_path}")
    time.sleep(0.05)  # Allow connection to establish
    sock.send_json(event)
    sock.close()
    ctx.term()


class TestAvatarEvent:
    def test_from_dict_state_change(self):
        e = AvatarEvent.from_dict({"event": "state_change", "state": "thinking"})
        assert e.event == "state_change"
        assert e.state == "thinking"

    def test_from_dict_speak_start(self):
        e = AvatarEvent.from_dict({
            "event": "speak_start",
            "text": "hello",
            "data": {"timestamps": []},
        })
        assert e.event == "speak_start"
        assert e.text == "hello"

    def test_from_dict_missing_event_raises(self):
        with pytest.raises(ValueError):
            AvatarEvent.from_dict({"state": "idle"})


class TestEventBus:
    def test_creates_pull_socket(self, event_bus, socket_path):
        event_bus.start()
        # Verify the socket file was created
        assert os.path.exists(socket_path)

    def test_receives_state_change(self, event_bus, socket_path):
        received = []
        event_bus.on_event = lambda e: received.append(e)
        event_bus.start()
        time.sleep(0.1)

        send_event(socket_path, {"event": "state_change", "state": "thinking"})
        time.sleep(0.2)

        assert len(received) == 1
        assert received[0].event == "state_change"
        assert received[0].state == "thinking"

    def test_receives_multiple_events(self, event_bus, socket_path):
        received = []
        event_bus.on_event = lambda e: received.append(e)
        event_bus.start()
        time.sleep(0.1)

        send_event(socket_path, {"event": "state_change", "state": "thinking"})
        send_event(socket_path, {"event": "state_change", "state": "idle"})
        time.sleep(0.3)

        assert len(received) == 2

    def test_stop_and_cleanup(self, event_bus, socket_path):
        event_bus.start()
        time.sleep(0.1)
        event_bus.stop()
        # Socket file should be cleaned up
        time.sleep(0.1)
        assert not os.path.exists(socket_path)

    def test_ignores_malformed_json(self, event_bus, socket_path):
        received = []
        event_bus.on_event = lambda e: received.append(e)
        event_bus.start()
        time.sleep(0.1)

        # Send malformed data
        ctx = zmq.Context()
        sock = ctx.socket(zmq.PUSH)
        sock.connect(f"ipc://{socket_path}")
        time.sleep(0.05)
        sock.send(b"not json")
        sock.close()
        ctx.term()

        # Then send a valid event
        send_event(socket_path, {"event": "state_change", "state": "idle"})
        time.sleep(0.2)

        # Should only have the valid event
        assert len(received) == 1
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_event_bus.py -v
```

Expected: ImportError

**Step 3: Implement event bus**

`src/avatar/event_bus.py`:
```python
"""ZeroMQ PUSH/PULL event bus for avatar IPC."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import zmq

log = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH = "/tmp/ascii-avatar.sock"


@dataclass
class AvatarEvent:
    event: str
    state: str = ""
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AvatarEvent:
        if "event" not in d:
            raise ValueError("Event dict must contain 'event' key")
        return cls(
            event=d["event"],
            state=d.get("state", ""),
            text=d.get("text", ""),
            data=d.get("data", {}),
        )


class EventBus:
    """Receives avatar events over a ZeroMQ PULL socket.

    Args:
        socket_path: Path for the Unix domain socket.
    """

    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH) -> None:
        self._socket_path = socket_path
        self._context: zmq.Context | None = None
        self._socket: zmq.Socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.on_event: Callable[[AvatarEvent], None] | None = None

    @property
    def socket_path(self) -> str:
        return self._socket_path

    def start(self) -> None:
        self._stop_event.clear()
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.PULL)
        self._socket.bind(f"ipc://{self._socket_path}")

        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    def _recv_loop(self) -> None:
        assert self._socket is not None
        poller = zmq.Poller()
        poller.register(self._socket, zmq.POLLIN)

        while not self._stop_event.is_set():
            socks = dict(poller.poll(timeout=100))
            if self._socket in socks:
                try:
                    raw = self._socket.recv(zmq.NOBLOCK)
                    data = json.loads(raw)
                    event = AvatarEvent.from_dict(data)
                    if self.on_event:
                        self.on_event(event)
                except (json.JSONDecodeError, ValueError) as e:
                    log.warning("Malformed event: %s", e)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._socket is not None:
            self._socket.close()
        if self._context is not None:
            self._context.term()
        # Clean up socket file
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_event_bus.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add src/avatar/event_bus.py tests/test_event_bus.py
git commit -m "feat: ZeroMQ PUSH/PULL event bus"
```

---

### Task 4: Personas

**Files:**
- Create: `src/avatar/personas.py`
- Create: `tests/test_personas.py`

**Step 1: Write failing tests**

`tests/test_personas.py`:
```python
from avatar.personas import Persona, get_persona, list_personas, DEFAULT_PERSONA


class TestPersona:
    def test_ghost_exists(self):
        p = get_persona("ghost")
        assert p.name == "ghost"
        assert p.frames == "cyberpunk"
        assert p.voice_engine == "kokoro"

    def test_oracle_exists(self):
        p = get_persona("oracle")
        assert p.voice_engine == "kokoro"

    def test_spectre_exists(self):
        p = get_persona("spectre")
        assert p.voice_engine == "elevenlabs"

    def test_unknown_persona_raises(self):
        import pytest
        with pytest.raises(KeyError):
            get_persona("nonexistent")

    def test_list_personas(self):
        names = list_personas()
        assert "ghost" in names
        assert "oracle" in names
        assert "spectre" in names

    def test_default_persona(self):
        assert DEFAULT_PERSONA == "ghost"

    def test_frame_rate_modifier(self):
        ghost = get_persona("ghost")
        oracle = get_persona("oracle")
        assert ghost.frame_rate_modifier == 1.0
        assert oracle.frame_rate_modifier < 1.0  # sage = slower
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_personas.py -v
```

**Step 3: Implement personas**

`src/avatar/personas.py`:
```python
"""Persona system — bundles frame set, voice, color, and personality."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    name: str
    frames: str
    voice_engine: str  # "kokoro" | "elevenlabs" | "piper"
    voice_id: str
    accent_color: str
    personality: str  # "minimal" | "sage" | "glitch"
    frame_rate_modifier: float


PERSONAS: dict[str, Persona] = {
    "ghost": Persona(
        name="ghost",
        frames="cyberpunk",
        voice_engine="kokoro",
        voice_id="af_bella",
        accent_color="cyan",
        personality="minimal",
        frame_rate_modifier=1.0,
    ),
    "oracle": Persona(
        name="oracle",
        frames="cyberpunk",
        voice_engine="kokoro",
        voice_id="bf_emma",
        accent_color="amber",
        personality="sage",
        frame_rate_modifier=0.8,
    ),
    "spectre": Persona(
        name="spectre",
        frames="cyberpunk",
        voice_engine="elevenlabs",
        voice_id="",
        accent_color="green",
        personality="glitch",
        frame_rate_modifier=1.3,
    ),
}

DEFAULT_PERSONA = "ghost"


def get_persona(name: str) -> Persona:
    return PERSONAS[name]


def list_personas() -> list[str]:
    return list(PERSONAS.keys())
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_personas.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add src/avatar/personas.py tests/test_personas.py
git commit -m "feat: persona system with ghost, oracle, spectre presets"
```

---

### Task 5: Cyberpunk Frame Set

**Files:**
- Create: `src/avatar/frames/cyberpunk.py`
- Update: `src/avatar/frames/__init__.py`
- Create: `tests/test_frames.py`

**Step 1: Write failing tests**

`tests/test_frames.py`:
```python
from avatar.frames import load_frame_set


class TestFrameSet:
    def test_load_cyberpunk(self):
        frames, rates = load_frame_set("cyberpunk")
        assert isinstance(frames, dict)
        assert isinstance(rates, dict)

    def test_all_states_present(self):
        frames, rates = load_frame_set("cyberpunk")
        for state in ["idle", "thinking", "speaking", "listening", "error"]:
            assert state in frames, f"Missing state: {state}"
            assert state in rates, f"Missing rate: {state}"

    def test_each_state_has_frames(self):
        frames, _ = load_frame_set("cyberpunk")
        for state, frame_list in frames.items():
            assert len(frame_list) >= 2, f"{state} needs at least 2 frames"
            for i, frame in enumerate(frame_list):
                assert isinstance(frame, str), f"{state}[{i}] is not a string"
                assert len(frame) > 0, f"{state}[{i}] is empty"

    def test_frame_rates_are_positive(self):
        _, rates = load_frame_set("cyberpunk")
        for state, rate in rates.items():
            assert rate > 0, f"{state} rate must be positive"

    def test_frames_fit_in_terminal(self):
        frames, _ = load_frame_set("cyberpunk")
        for state, frame_list in frames.items():
            for i, frame in enumerate(frame_list):
                lines = frame.strip("\n").split("\n")
                assert len(lines) <= 25, f"{state}[{i}] too tall: {len(lines)} lines"

    def test_unknown_frame_set_raises(self):
        import pytest
        with pytest.raises(KeyError):
            load_frame_set("nonexistent")
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_frames.py -v
```

**Step 3: Implement frame loader**

Update `src/avatar/frames/__init__.py`:
```python
"""ASCII art frame set loader."""

from __future__ import annotations

from typing import Any


def load_frame_set(name: str) -> tuple[dict[str, list[str]], dict[str, float]]:
    """Load a frame set by name. Returns (frames_dict, rates_dict)."""
    if name == "cyberpunk":
        from avatar.frames.cyberpunk import FRAMES, FRAME_RATES
        return FRAMES, FRAME_RATES
    raise KeyError(f"Unknown frame set: {name}")
```

**Step 4: Create cyberpunk frame set**

`src/avatar/frames/cyberpunk.py`:

This file is large (~500+ lines). The implementing agent should create a cyberpunk-themed ASCII face using:
- Unicode box-drawing: `┌ ─ ┐ │ └ ┘ ├ ┤ ┬ ┴ ┼`
- Block elements: `▓ ░ ▒ █ ▄ ▀ ▐ ▌`
- Braille dots for detail
- ANSI escape codes for cyan accents: `\033[36m` (cyan), `\033[0m` (reset), `\033[31m` (red for error)

Requirements per state:
- **idle**: 3 frames, subtle breathing/pulse — slight variation in border brightness
- **thinking**: 4 frames, scan-line effect sweeping down, eyes flickering
- **speaking**: 4 frames, mouth area cycling through 4 positions (closed, slightly open, open, wide)
- **listening**: 3 frames, side elements pulsing, eyes wide
- **error**: 2 frames, glitch/corruption effect with red tint

Each frame: ~20 lines tall, ~40 chars wide. The face should have a recognizable structure: forehead area with circuit patterns, eyes (the most expressive part), nose/center area, mouth, jaw/chin, and side frame elements.

```python
"""Cyberpunk ASCII avatar frame set.

A terminal-native AI face — Ghost in the Shell meets Blade Runner.
Uses Unicode box-drawing, block elements, and ANSI color for cyan accents.
"""

# ANSI color codes
C = "\033[36m"   # cyan
R = "\033[31m"   # red
D = "\033[2m"    # dim
B = "\033[1m"    # bold
X = "\033[0m"    # reset

# ============================================================
# IDLE — 3 frames, subtle breathing pulse
# ============================================================

_IDLE_1 = f"""
{C}┌──────────────────────────────────────┐{X}
{C}│{X}  ░░▒▒▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▒▒░░  {C}│{X}
{C}│{X}  ░▒  {C}╔══════════════════════╗{X}  ▒░  {C}│{X}
{C}│{X}  ░▒  {C}║{X}  ▄▄▄▄    ▄▄▄▄  ▄▄  {C}║{X}  ▒░  {C}│{X}
{C}│{X}  ░▒  {C}║{X} ┌────┐  ┌────┐ ░░  {C}║{X}  ▒░  {C}│{X}
{C}│{X}  ░▒  {C}║{X} │{B}●  ●{X}│  │{B}●  ●{X}│ ▒▒  {C}║{X}  ▒░  {C}│{X}
{C}│{X}  ░▒  {C}║{X} └────┘  └────┘ ░░  {C}║{X}  ▒░  {C}│{X}
{C}│{X}  ░▒  {C}║{X}       ▄▄▄       ▒▒  {C}║{X}  ▒░  {C}│{X}
{C}│{X}  ░▒  {C}║{X}      │   │      ░░  {C}║{X}  ▒░  {C}│{X}
{C}│{X}  ░▒  {C}║{X}    ┌───────┐    ▒▒  {C}║{X}  ▒░  {C}│{X}
{C}│{X}  ░▒  {C}║{X}    │ ───── │    ░░  {C}║{X}  ▒░  {C}│{X}
{C}│{X}  ░▒  {C}║{X}    └───────┘    ▒▒  {C}║{X}  ▒░  {C}│{X}
{C}│{X}  ░▒  {C}╚══════════════════════╝{X}  ▒░  {C}│{X}
{C}│{X}  ░░▒▒▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▒▒░░  {C}│{X}
{C}│{X}       {D}▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄{X}       {C}│{X}
{C}└──────────────────────────────────────┘{X}
"""

# The implementing agent should create all remaining frames following this pattern.
# Idle frames 2-3: vary the border brightness (▒ ↔ ░) and circuit pattern density.
# Thinking frames 1-4: add a scan-line (▓▓▓▓▓▓▓▓) that moves down one row per frame,
#   eyes flicker between ● and ○.
# Speaking frames 1-4: mouth cycles: ───── → ─╌─╌─ → ╌     ╌ → ─────
# Listening frames 1-3: side ░▒ elements pulse brighter, eyes wider (◉ instead of ●)
# Error frames 1-2: red tint, glitch characters (╪ ╫ ╬), displaced rows

FRAMES = {
    "idle": [_IDLE_1],       # Agent: expand to 3 frames
    "thinking": [],          # Agent: create 4 frames
    "speaking": [],          # Agent: create 4 frames
    "listening": [],         # Agent: create 3 frames
    "error": [],             # Agent: create 2 frames
}

FRAME_RATES = {
    "idle": 0.8,
    "thinking": 0.15,
    "speaking": 0.1,
    "listening": 0.4,
    "error": 0.2,
}
```

The implementing agent MUST fill in all frames. The template above shows the style — every frame must follow the same dimensions and structural layout. Only the expressive elements (eyes, mouth, borders, scan-lines) change between frames.

**Step 5: Run tests**

```bash
python -m pytest tests/test_frames.py -v
```

Expected: All PASS

**Step 6: Commit**

```bash
git add src/avatar/frames/ tests/test_frames.py
git commit -m "feat: cyberpunk ASCII frame set with 5 states"
```

---

### Task 6: Renderer

**Files:**
- Create: `src/avatar/renderer.py`
- Create: `tests/test_renderer.py`

**Step 1: Write failing tests**

`tests/test_renderer.py`:
```python
import time
import pytest

from avatar.renderer import AvatarRenderer
from avatar.state_machine import AvatarState


class FakeTerminal:
    """Fake blessed terminal for headless testing."""

    def __init__(self, width=80, height=24, colors=256):
        self.width = width
        self.height = height
        self.number_of_colors = colors
        self.output = []
        self._location_ctx = self

    def clear(self):
        self.output.append("CLEAR")
        return ""

    def move_xy(self, x, y):
        return f"MOVE({x},{y})"

    def home(self):
        return "HOME"

    def normal(self):
        return "NORMAL"

    def hidden_cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def location(self, x=0, y=0):
        return self

    def inkey(self, timeout=0):
        class Key:
            def __init__(self):
                self.name = None
            def __eq__(self, other):
                return False
        return Key()


class TestRenderer:
    def test_create_renderer(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk")
        assert r is not None

    def test_current_frame_changes_with_state(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk")
        frame_idle = r.get_current_frame(AvatarState.IDLE, frame_index=0)
        frame_think = r.get_current_frame(AvatarState.THINKING, frame_index=0)
        # Different states should produce different frames
        assert frame_idle != frame_think

    def test_frame_index_cycles(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk")
        idx = r.next_frame_index(AvatarState.IDLE, current_index=0)
        # Should cycle within the idle frame count
        assert isinstance(idx, int)
        assert idx >= 0

    def test_status_bar_content(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk")
        bar = r.format_status_bar(
            state=AvatarState.IDLE,
            connected=True,
            tts_loaded=False,
            last_event="state_change",
        )
        assert "IDLE" in bar
        assert "connected" in bar.lower() or "●" in bar

    def test_monochrome_fallback(self):
        term = FakeTerminal(colors=2)
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk")
        frame = r.get_current_frame(AvatarState.IDLE, frame_index=0)
        # ANSI color codes should be stripped
        assert "\033[36m" not in frame

    def test_frame_rate_from_state(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk", frame_rate_modifier=1.0)
        rate = r.get_frame_rate(AvatarState.IDLE)
        assert rate == pytest.approx(0.8, abs=0.01)

    def test_frame_rate_modifier(self):
        term = FakeTerminal()
        r = AvatarRenderer(terminal=term, frame_set="cyberpunk", frame_rate_modifier=0.5)
        rate = r.get_frame_rate(AvatarState.IDLE)
        assert rate == pytest.approx(0.4, abs=0.01)
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_renderer.py -v
```

**Step 3: Implement renderer**

`src/avatar/renderer.py`:
```python
"""Terminal renderer for ASCII avatar using blessed."""

from __future__ import annotations

import re
import time
from typing import Any

from avatar.frames import load_frame_set
from avatar.state_machine import AvatarState

ANSI_ESCAPE = re.compile(r"\033\[[0-9;]*m")


class AvatarRenderer:
    """Renders ASCII art frames to a terminal.

    Args:
        terminal: A blessed Terminal instance (or fake for testing).
        frame_set: Name of the frame set to load.
        frame_rate_modifier: Multiplier on base frame rates (from persona).
    """

    def __init__(
        self,
        terminal: Any,
        frame_set: str = "cyberpunk",
        frame_rate_modifier: float = 1.0,
    ) -> None:
        self._term = terminal
        self._frames, self._rates = load_frame_set(frame_set)
        self._modifier = frame_rate_modifier
        self._supports_color = getattr(terminal, "number_of_colors", 0) >= 256

    def get_current_frame(self, state: AvatarState, frame_index: int) -> str:
        frames = self._frames.get(state.value, self._frames["idle"])
        if not frames:
            frames = self._frames["idle"]
        idx = frame_index % len(frames)
        frame = frames[idx]
        if not self._supports_color:
            frame = ANSI_ESCAPE.sub("", frame)
        return frame

    def next_frame_index(self, state: AvatarState, current_index: int) -> int:
        frames = self._frames.get(state.value, self._frames["idle"])
        if not frames:
            return 0
        return (current_index + 1) % len(frames)

    def get_frame_rate(self, state: AvatarState) -> float:
        base = self._rates.get(state.value, 0.8)
        return base * self._modifier

    def format_status_bar(
        self,
        state: AvatarState,
        connected: bool,
        tts_loaded: bool,
        last_event: str = "",
    ) -> str:
        conn = "● connected" if connected else "○ waiting"
        tts = "♪ TTS" if tts_loaded else "♪ no TTS"
        return f" {state.value.upper()} │ {conn} │ {tts} │ last: {last_event} "

    def render_frame(self, frame: str, status_bar: str) -> None:
        """Render a frame and status bar to the terminal."""
        with self._term.hidden_cursor():
            print(self._term.home + self._term.clear(), end="")
            print(frame)
            # Status bar at bottom
            y = self._term.height - 1
            with self._term.location(0, y):
                print(status_bar[:self._term.width], end="", flush=True)
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_renderer.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add src/avatar/renderer.py tests/test_renderer.py
git commit -m "feat: terminal renderer with frame cycling and status bar"
```

---

### Task 7: Bridge — CLI & Claude Code Sender

**Files:**
- Create: `src/avatar/bridge/claude_code.py`
- Create: `src/avatar/bridge/cli.py`
- Create: `src/avatar/bridge/hooks.py`
- Create: `tests/test_bridge.py`

**Step 1: Write failing tests**

`tests/test_bridge.py`:
```python
import json
import os
import threading
import time

import pytest
import zmq

from avatar.bridge.claude_code import ClaudeCodeBridge
from avatar.bridge.hooks import think, respond, listen, idle, error


@pytest.fixture
def socket_path(tmp_path):
    return str(tmp_path / "test-bridge.sock")


@pytest.fixture
def pull_receiver(socket_path):
    """Set up a PULL socket to receive events from the bridge."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PULL)
    sock.bind(f"ipc://{socket_path}")
    yield sock
    sock.close()
    ctx.term()


class TestClaudeCodeBridge:
    def test_send_thinking(self, socket_path, pull_receiver):
        bridge = ClaudeCodeBridge(socket_path=socket_path)
        bridge.connect()
        time.sleep(0.05)
        bridge.send_thinking()
        time.sleep(0.1)

        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["event"] == "state_change"
        assert msg["state"] == "thinking"
        bridge.disconnect()

    def test_send_speaking(self, socket_path, pull_receiver):
        bridge = ClaudeCodeBridge(socket_path=socket_path)
        bridge.connect()
        time.sleep(0.05)
        bridge.send_speaking("hello world")
        time.sleep(0.1)

        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["event"] == "speak_start"
        assert msg["text"] == "hello world"
        bridge.disconnect()

    def test_send_listening(self, socket_path, pull_receiver):
        bridge = ClaudeCodeBridge(socket_path=socket_path)
        bridge.connect()
        time.sleep(0.05)
        bridge.send_listening()
        time.sleep(0.1)

        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["state"] == "listening"
        bridge.disconnect()

    def test_send_idle(self, socket_path, pull_receiver):
        bridge = ClaudeCodeBridge(socket_path=socket_path)
        bridge.connect()
        time.sleep(0.05)
        bridge.send_idle()
        time.sleep(0.1)

        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["state"] == "idle"
        bridge.disconnect()

    def test_send_error(self, socket_path, pull_receiver):
        bridge = ClaudeCodeBridge(socket_path=socket_path)
        bridge.connect()
        time.sleep(0.05)
        bridge.send_error("something broke")
        time.sleep(0.1)

        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["state"] == "error"
        assert msg["data"]["message"] == "something broke"
        bridge.disconnect()

    def test_context_manager(self, socket_path, pull_receiver):
        with ClaudeCodeBridge(socket_path=socket_path) as bridge:
            bridge.send_thinking()
        time.sleep(0.1)
        msg = pull_receiver.recv_json(zmq.NOBLOCK)
        assert msg["state"] == "thinking"
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_bridge.py -v
```

**Step 3: Implement bridge components**

`src/avatar/bridge/claude_code.py`:
```python
"""Claude Code bridge — sends events to the avatar process via PUSH socket."""

from __future__ import annotations

import zmq

from avatar.event_bus import DEFAULT_SOCKET_PATH


class ClaudeCodeBridge:
    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH) -> None:
        self._socket_path = socket_path
        self._context: zmq.Context | None = None
        self._socket: zmq.Socket | None = None

    def connect(self) -> None:
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.PUSH)
        self._socket.connect(f"ipc://{self._socket_path}")

    def disconnect(self) -> None:
        if self._socket:
            self._socket.close()
        if self._context:
            self._context.term()
        self._socket = None
        self._context = None

    def __enter__(self) -> ClaudeCodeBridge:
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    def _send(self, event: dict) -> None:
        assert self._socket is not None, "Not connected"
        self._socket.send_json(event)

    def send_thinking(self) -> None:
        self._send({"event": "state_change", "state": "thinking"})

    def send_speaking(self, text: str) -> None:
        self._send({"event": "speak_start", "state": "speaking", "text": text})

    def send_listening(self) -> None:
        self._send({"event": "state_change", "state": "listening"})

    def send_idle(self) -> None:
        self._send({"event": "state_change", "state": "idle"})

    def send_error(self, message: str) -> None:
        self._send({
            "event": "state_change",
            "state": "error",
            "data": {"message": message},
        })
```

`src/avatar/bridge/hooks.py`:
```python
"""Thin wrappers for Claude Code hooks — connect, send, disconnect."""

from avatar.bridge.claude_code import ClaudeCodeBridge
from avatar.event_bus import DEFAULT_SOCKET_PATH


def think(socket_path: str = DEFAULT_SOCKET_PATH) -> None:
    with ClaudeCodeBridge(socket_path=socket_path) as bridge:
        bridge.send_thinking()


def respond(text: str, socket_path: str = DEFAULT_SOCKET_PATH) -> None:
    with ClaudeCodeBridge(socket_path=socket_path) as bridge:
        bridge.send_speaking(text)


def listen(socket_path: str = DEFAULT_SOCKET_PATH) -> None:
    with ClaudeCodeBridge(socket_path=socket_path) as bridge:
        bridge.send_listening()


def idle(socket_path: str = DEFAULT_SOCKET_PATH) -> None:
    with ClaudeCodeBridge(socket_path=socket_path) as bridge:
        bridge.send_idle()


def error(message: str, socket_path: str = DEFAULT_SOCKET_PATH) -> None:
    with ClaudeCodeBridge(socket_path=socket_path) as bridge:
        bridge.send_error(message)
```

`src/avatar/bridge/cli.py`:
```python
"""Argparse CLI entry point for the avatar bridge."""

import argparse
import sys

from avatar.bridge import hooks


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="avatar-bridge",
        description="Send events to the ASCII avatar process",
    )
    parser.add_argument(
        "--socket", default="/tmp/ascii-avatar.sock",
        help="Path to the avatar Unix socket",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("think", help="Signal thinking state")

    speak_parser = sub.add_parser("speak", help="Speak text")
    speak_parser.add_argument("text", nargs="+", help="Text to speak")

    sub.add_parser("listen", help="Signal listening state")
    sub.add_parser("idle", help="Return to idle")

    error_parser = sub.add_parser("error", help="Signal error state")
    error_parser.add_argument("message", nargs="*", default=["Unknown error"])

    args = parser.parse_args(argv)

    if args.command == "think":
        hooks.think(socket_path=args.socket)
    elif args.command == "speak":
        hooks.respond(" ".join(args.text), socket_path=args.socket)
    elif args.command == "listen":
        hooks.listen(socket_path=args.socket)
    elif args.command == "idle":
        hooks.idle(socket_path=args.socket)
    elif args.command == "error":
        hooks.error(" ".join(args.message), socket_path=args.socket)


if __name__ == "__main__":
    main()
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_bridge.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add src/avatar/bridge/ tests/test_bridge.py
git commit -m "feat: bridge CLI, hooks, and ClaudeCodeBridge sender"
```

---

### Task 8: Voice — Abstract Interface & Audio Player

**Files:**
- Create: `src/avatar/voice/base.py`
- Create: `src/avatar/voice/audio_player.py`
- Create: `tests/test_audio_player.py`

**Step 1: Write failing tests**

`tests/test_audio_player.py`:
```python
import numpy as np
import pytest

from avatar.voice.base import TTSEngine, WordTiming
from avatar.voice.audio_player import AudioPlayer


class TestWordTiming:
    def test_create(self):
        wt = WordTiming(word="hello", start=0.0, end=0.5)
        assert wt.word == "hello"

    def test_duration(self):
        wt = WordTiming(word="hello", start=0.1, end=0.6)
        assert wt.duration == pytest.approx(0.5)


class TestAudioPlayer:
    def test_create(self):
        player = AudioPlayer()
        assert player.is_playing is False

    def test_play_silence(self):
        player = AudioPlayer()
        # 0.1s of silence at 24000 Hz
        audio = np.zeros(2400, dtype=np.float32)
        player.play(audio, sample_rate=24000)
        assert player.is_playing is True
        player.stop()

    def test_stop_when_not_playing(self):
        player = AudioPlayer()
        player.stop()  # Should not raise

    def test_word_callbacks(self):
        player = AudioPlayer()
        callbacks = []
        timings = [
            WordTiming("hello", 0.0, 0.05),
            WordTiming("world", 0.05, 0.1),
        ]
        audio = np.zeros(2400, dtype=np.float32)  # 0.1s
        player.play(
            audio,
            sample_rate=24000,
            word_timings=timings,
            on_word=lambda wt: callbacks.append(wt.word),
        )
        import time
        time.sleep(0.3)
        player.stop()
        # Callbacks should have fired
        assert "hello" in callbacks
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_audio_player.py -v
```

**Step 3: Implement abstract interface and audio player**

`src/avatar/voice/base.py`:
```python
"""Abstract TTS engine interface and shared types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generator

import numpy as np


@dataclass
class WordTiming:
    word: str
    start: float  # seconds
    end: float    # seconds

    @property
    def duration(self) -> float:
        return self.end - self.start


class TTSEngine(ABC):
    @abstractmethod
    def synthesize(self, text: str) -> tuple[np.ndarray, list[WordTiming]]:
        """Synthesize text to audio array + word timings."""
        ...

    @abstractmethod
    def stream_synthesize(
        self, text: str
    ) -> Generator[tuple[np.ndarray, WordTiming | None], None, None]:
        """Stream synthesis — yields (audio_chunk, optional word_timing)."""
        ...

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this engine is ready (model loaded, API key present, etc)."""
        ...
```

`src/avatar/voice/audio_player.py`:
```python
"""Non-blocking audio playback with word-timing callbacks."""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

from avatar.voice.base import WordTiming


class AudioPlayer:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._playing = False

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(
        self,
        audio: np.ndarray,
        sample_rate: int,
        word_timings: list[WordTiming] | None = None,
        on_word: Callable[[WordTiming], None] | None = None,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        self.stop()
        self._stop_event.clear()
        self._playing = True

        self._thread = threading.Thread(
            target=self._play_thread,
            args=(audio, sample_rate, word_timings or [], on_word, on_complete),
            daemon=True,
        )
        self._thread.start()

    def _play_thread(
        self,
        audio: np.ndarray,
        sample_rate: int,
        word_timings: list[WordTiming],
        on_word: Callable[[WordTiming], None] | None,
        on_complete: Callable[[], None] | None,
    ) -> None:
        try:
            # Start playback
            sd.play(audio, samplerate=sample_rate)

            # Fire word callbacks at the right times
            if on_word and word_timings:
                start_time = time.monotonic()
                for wt in word_timings:
                    if self._stop_event.is_set():
                        break
                    wait = wt.start - (time.monotonic() - start_time)
                    if wait > 0:
                        self._stop_event.wait(timeout=wait)
                    if not self._stop_event.is_set():
                        on_word(wt)

            # Wait for playback to finish
            if not self._stop_event.is_set():
                sd.wait()

            if on_complete and not self._stop_event.is_set():
                on_complete()
        except Exception:
            pass  # Audio device unavailable — graceful degradation
        finally:
            self._playing = False

    def stop(self) -> None:
        self._stop_event.set()
        sd.stop()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._playing = False
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_audio_player.py -v
```

Expected: All PASS (sounddevice plays silence to default device — if no audio device, the test still passes due to the try/except)

**Step 5: Commit**

```bash
git add src/avatar/voice/base.py src/avatar/voice/audio_player.py tests/test_audio_player.py
git commit -m "feat: abstract TTS interface and non-blocking audio player"
```

---

### Task 9: Kokoro TTS Engine

**Files:**
- Create: `src/avatar/voice/kokoro_engine.py`
- Create: `tests/test_kokoro_engine.py`

**Step 1: Write failing tests**

`tests/test_kokoro_engine.py`:
```python
import pytest
from avatar.voice.kokoro_engine import KokoroEngine


class TestKokoroEngine:
    def test_create(self):
        engine = KokoroEngine()
        assert engine.sample_rate == 24000

    def test_is_available_without_model(self):
        engine = KokoroEngine(model_path="/nonexistent/path")
        assert engine.is_available() is False

    def test_estimate_word_timings(self):
        engine = KokoroEngine()
        timings = engine.estimate_word_timings("hello world", total_duration=1.0)
        assert len(timings) == 2
        assert timings[0].word == "hello"
        assert timings[1].word == "world"
        assert timings[0].start == pytest.approx(0.0)
        assert timings[1].end == pytest.approx(1.0, abs=0.01)

    def test_estimate_preserves_order(self):
        engine = KokoroEngine()
        timings = engine.estimate_word_timings(
            "one two three four", total_duration=2.0
        )
        for i in range(len(timings) - 1):
            assert timings[i].end <= timings[i + 1].start + 0.001

    def test_estimate_empty_text(self):
        engine = KokoroEngine()
        timings = engine.estimate_word_timings("", total_duration=1.0)
        assert timings == []
```

Note: We test the estimation fallback and availability check here — actual synthesis requires the Kokoro model which may not be installed. The implementing agent should add a test that runs actual synthesis only if the model exists:

```python
    @pytest.mark.skipif(
        not KokoroEngine().is_available(),
        reason="Kokoro model not installed",
    )
    def test_synthesize_real(self):
        engine = KokoroEngine()
        audio, timings = engine.synthesize("Hello world")
        assert len(audio) > 0
        assert len(timings) >= 1
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_kokoro_engine.py -v
```

**Step 3: Implement Kokoro engine**

`src/avatar/voice/kokoro_engine.py`:
```python
"""Kokoro TTS engine — local, fast, native phoneme output."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Generator

import numpy as np

from avatar.voice.base import TTSEngine, WordTiming

log = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = Path.home() / ".cache" / "ascii-avatar" / "models"
DEFAULT_VOICE = "af_bella"
SAMPLE_RATE = 24000


class KokoroEngine(TTSEngine):
    """Kokoro-ONNX TTS engine.

    Lazy-loads the model on first synthesis call.
    Falls back to proportional timing if native phoneme output unavailable.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        voice: str = DEFAULT_VOICE,
    ) -> None:
        self._model_dir = Path(model_path) if model_path else DEFAULT_MODEL_DIR
        self._voice = voice
        self._model = None  # Lazy loaded

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    def is_available(self) -> bool:
        model_file = self._model_dir / "kokoro-v1.0.onnx"
        voices_file = self._model_dir / "voices-v1.0.bin"
        return model_file.exists() and voices_file.exists()

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            from kokoro_onnx import Kokoro

            model_file = str(self._model_dir / "kokoro-v1.0.onnx")
            voices_file = str(self._model_dir / "voices-v1.0.bin")
            self._model = Kokoro(model_file, voices_file)
            log.info("Kokoro model loaded from %s", self._model_dir)
        except Exception as e:
            log.error(
                "Failed to load Kokoro model: %s. "
                "Run scripts/install.sh to download models.",
                e,
            )
            raise

    def synthesize(self, text: str) -> tuple[np.ndarray, list[WordTiming]]:
        self._load_model()
        assert self._model is not None

        # Kokoro returns (samples, sample_rate) or yields (gs, ps, audio)
        samples, sr = self._model.create(text, voice=self._voice, speed=1.0)
        duration = len(samples) / sr
        timings = self.estimate_word_timings(text, duration)
        return samples, timings

    def stream_synthesize(
        self, text: str
    ) -> Generator[tuple[np.ndarray, WordTiming | None], None, None]:
        self._load_model()
        assert self._model is not None

        try:
            # Try streaming API if available
            for gs, ps, audio in self._model.create(
                text, voice=self._voice, speed=1.0, is_stream=True
            ):
                wt = WordTiming(word=gs, start=0.0, end=0.0) if gs else None
                yield audio, wt
        except TypeError:
            # Fallback to non-streaming
            audio, timings = self.synthesize(text)
            for wt in timings:
                start_sample = int(wt.start * self.sample_rate)
                end_sample = int(wt.end * self.sample_rate)
                chunk = audio[start_sample:end_sample]
                yield chunk, wt

    def estimate_word_timings(
        self, text: str, total_duration: float
    ) -> list[WordTiming]:
        """Estimate word timings proportionally by character count."""
        words = text.split()
        if not words:
            return []

        total_chars = sum(len(w) for w in words)
        if total_chars == 0:
            return []

        timings = []
        current_time = 0.0
        for word in words:
            word_duration = (len(word) / total_chars) * total_duration
            timings.append(WordTiming(
                word=word,
                start=current_time,
                end=current_time + word_duration,
            ))
            current_time += word_duration

        return timings
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_kokoro_engine.py -v
```

Expected: All non-skip tests PASS

**Step 5: Commit**

```bash
git add src/avatar/voice/kokoro_engine.py tests/test_kokoro_engine.py
git commit -m "feat: Kokoro TTS engine with lazy loading and timing estimation"
```

---

### Task 10: ElevenLabs TTS Engine

**Files:**
- Create: `src/avatar/voice/elevenlabs_engine.py`
- Create: `tests/test_elevenlabs_engine.py`

**Step 1: Write failing tests**

`tests/test_elevenlabs_engine.py`:
```python
import os
import pytest
from avatar.voice.elevenlabs_engine import ElevenLabsEngine


class TestElevenLabsEngine:
    def test_create(self):
        engine = ElevenLabsEngine(voice_id="test-voice")
        assert engine.sample_rate == 24000

    def test_not_available_without_key(self):
        # Temporarily remove key if set
        key = os.environ.pop("ELEVENLABS_API_KEY", None)
        try:
            engine = ElevenLabsEngine(voice_id="test")
            assert engine.is_available() is False
        finally:
            if key:
                os.environ["ELEVENLABS_API_KEY"] = key
```

**Step 2: Run to verify fail, then implement**

`src/avatar/voice/elevenlabs_engine.py`:
```python
"""ElevenLabs TTS engine — cloud, opt-in, requires API key."""

from __future__ import annotations

import logging
import os
from typing import Generator

import numpy as np

from avatar.voice.base import TTSEngine, WordTiming

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000


class ElevenLabsEngine(TTSEngine):
    """ElevenLabs cloud TTS. Requires ELEVENLABS_API_KEY env var."""

    def __init__(self, voice_id: str = "") -> None:
        self._voice_id = voice_id
        self._client = None

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    def is_available(self) -> bool:
        return bool(os.environ.get("ELEVENLABS_API_KEY"))

    def _get_client(self):
        if self._client is None:
            try:
                from elevenlabs import ElevenLabs

                self._client = ElevenLabs(
                    api_key=os.environ["ELEVENLABS_API_KEY"]
                )
            except ImportError:
                log.error("elevenlabs package not installed. pip install elevenlabs")
                raise
            except KeyError:
                log.error("ELEVENLABS_API_KEY not set")
                raise
        return self._client

    def synthesize(self, text: str) -> tuple[np.ndarray, list[WordTiming]]:
        client = self._get_client()
        response = client.text_to_speech.convert(
            text=text,
            voice_id=self._voice_id,
            output_format="pcm_24000",
        )
        # Collect audio bytes
        audio_bytes = b"".join(response)
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        duration = len(audio) / self.sample_rate

        # Estimate timings (ElevenLabs streaming API has real timestamps,
        # but the simple convert endpoint does not)
        timings = self._estimate_timings(text, duration)
        return audio, timings

    def stream_synthesize(
        self, text: str
    ) -> Generator[tuple[np.ndarray, WordTiming | None], None, None]:
        # For streaming with timestamps, use the websocket API
        # Fallback: synthesize full then yield chunks
        audio, timings = self.synthesize(text)
        for wt in timings:
            start = int(wt.start * self.sample_rate)
            end = int(wt.end * self.sample_rate)
            yield audio[start:end], wt

    def _estimate_timings(
        self, text: str, duration: float
    ) -> list[WordTiming]:
        words = text.split()
        if not words:
            return []
        total_chars = sum(len(w) for w in words)
        if total_chars == 0:
            return []
        timings = []
        t = 0.0
        for word in words:
            d = (len(word) / total_chars) * duration
            timings.append(WordTiming(word=word, start=t, end=t + d))
            t += d
        return timings
```

**Step 3: Run tests**

```bash
python -m pytest tests/test_elevenlabs_engine.py -v
```

**Step 4: Commit**

```bash
git add src/avatar/voice/elevenlabs_engine.py tests/test_elevenlabs_engine.py
git commit -m "feat: ElevenLabs TTS engine (cloud, opt-in)"
```

---

### Task 11: Piper TTS Engine

**Files:**
- Create: `src/avatar/voice/piper_engine.py`
- Create: `tests/test_piper_engine.py`

**Step 1: Write failing tests**

`tests/test_piper_engine.py`:
```python
from avatar.voice.piper_engine import PiperEngine


class TestPiperEngine:
    def test_create(self):
        engine = PiperEngine()
        assert engine.sample_rate == 22050

    def test_not_available_without_model(self):
        engine = PiperEngine(model_path="/nonexistent")
        assert engine.is_available() is False
```

**Step 2: Implement**

`src/avatar/voice/piper_engine.py`:
```python
"""Piper TTS engine — ultra-lightweight fallback. GPL-3.0 licensed."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Generator

import numpy as np

from avatar.voice.base import TTSEngine, WordTiming

log = logging.getLogger(__name__)

SAMPLE_RATE = 22050


class PiperEngine(TTSEngine):
    """Piper TTS fallback engine. Requires piper-tts package and model file."""

    def __init__(self, model_path: str | Path | None = None) -> None:
        self._model_path = Path(model_path) if model_path else None
        self._voice = None

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    def is_available(self) -> bool:
        if self._model_path is None:
            return False
        try:
            import piper  # noqa: F401
            return self._model_path.exists()
        except ImportError:
            return False

    def _load(self):
        if self._voice is not None:
            return
        from piper import PiperVoice
        self._voice = PiperVoice.load(str(self._model_path))

    def synthesize(self, text: str) -> tuple[np.ndarray, list[WordTiming]]:
        self._load()
        assert self._voice is not None
        audio_bytes = b""
        for chunk in self._voice.synthesize_stream_raw(text):
            audio_bytes += chunk
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        duration = len(audio) / self.sample_rate
        timings = self._estimate_timings(text, duration)
        return audio, timings

    def stream_synthesize(
        self, text: str
    ) -> Generator[tuple[np.ndarray, WordTiming | None], None, None]:
        audio, timings = self.synthesize(text)
        for wt in timings:
            start = int(wt.start * self.sample_rate)
            end = int(wt.end * self.sample_rate)
            yield audio[start:end], wt

    def _estimate_timings(self, text: str, duration: float) -> list[WordTiming]:
        words = text.split()
        if not words:
            return []
        total = sum(len(w) for w in words)
        if total == 0:
            return []
        timings, t = [], 0.0
        for w in words:
            d = (len(w) / total) * duration
            timings.append(WordTiming(word=w, start=t, end=t + d))
            t += d
        return timings
```

**Step 3: Run tests and commit**

```bash
python -m pytest tests/test_piper_engine.py -v
git add src/avatar/voice/piper_engine.py tests/test_piper_engine.py
git commit -m "feat: Piper TTS fallback engine"
```

---

### Task 12: Main Entry Point

**Files:**
- Create: `src/avatar/main.py`

**Step 1: Implement main.py**

`src/avatar/main.py`:
```python
"""ASCII Avatar — entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from avatar.event_bus import AvatarEvent, EventBus
from avatar.personas import DEFAULT_PERSONA, get_persona, list_personas
from avatar.renderer import AvatarRenderer
from avatar.state_machine import AvatarState, AvatarStateMachine
from avatar.voice.audio_player import AudioPlayer
from avatar.voice.base import TTSEngine

log = logging.getLogger(__name__)


def resolve_tts_engine(persona) -> TTSEngine | None:
    """Resolve TTS engine from persona config. Returns None if unavailable."""
    if persona.voice_engine == "kokoro":
        from avatar.voice.kokoro_engine import KokoroEngine
        engine = KokoroEngine(voice=persona.voice_id)
        if engine.is_available():
            return engine
        log.warning(
            "Kokoro model not found. Run scripts/install.sh to download. "
            "Running in animation-only mode."
        )
        return None
    elif persona.voice_engine == "elevenlabs":
        from avatar.voice.elevenlabs_engine import ElevenLabsEngine
        engine = ElevenLabsEngine(voice_id=persona.voice_id)
        if engine.is_available():
            return engine
        log.warning(
            "ELEVENLABS_API_KEY not set. Falling back to Kokoro."
        )
        # Fall back to kokoro
        from avatar.voice.kokoro_engine import KokoroEngine
        fallback = KokoroEngine(voice=persona.voice_id)
        return fallback if fallback.is_available() else None
    elif persona.voice_engine == "piper":
        from avatar.voice.piper_engine import PiperEngine
        engine = PiperEngine()
        return engine if engine.is_available() else None
    return None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ASCII Avatar for Claude Code")
    parser.add_argument(
        "--persona", default=DEFAULT_PERSONA,
        choices=list_personas(),
        help=f"Persona preset (default: {DEFAULT_PERSONA})",
    )
    parser.add_argument(
        "--socket", default="/tmp/ascii-avatar.sock",
        help="Unix socket path for event bus",
    )
    parser.add_argument("--no-voice", action="store_true", help="Disable TTS")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument(
        "--voice", default=None,
        help="Override persona voice ID",
    )
    parser.add_argument(
        "--audio-device", default=None, type=int,
        help="Override audio output device index",
    )
    parser.add_argument("--compact", action="store_true", help="Compact mode")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    persona = get_persona(args.persona)
    if args.voice:
        # Override voice from persona
        from avatar.personas import Persona
        persona = Persona(
            name=persona.name, frames=persona.frames,
            voice_engine=persona.voice_engine, voice_id=args.voice,
            accent_color=persona.accent_color, personality=persona.personality,
            frame_rate_modifier=persona.frame_rate_modifier,
        )

    # Audio device override
    if args.audio_device is not None:
        import sounddevice as sd
        sd.default.device = args.audio_device

    # TTS engine
    tts: TTSEngine | None = None
    if not args.no_voice:
        tts = resolve_tts_engine(persona)

    audio_player = AudioPlayer()

    # State machine
    sm = AvatarStateMachine(idle_timeout=30)

    # Renderer
    import blessed
    term = blessed.Terminal()
    if args.no_color:
        term.number_of_colors = 2
    renderer = AvatarRenderer(
        terminal=term,
        frame_set=persona.frames,
        frame_rate_modifier=persona.frame_rate_modifier,
    )

    # Event bus
    bus = EventBus(socket_path=args.socket)
    connected = False

    def handle_event(event: AvatarEvent) -> None:
        nonlocal connected
        connected = True
        if event.event == "state_change":
            try:
                new_state = AvatarState(event.state)
                sm.transition(new_state)
            except ValueError:
                log.warning("Unknown state: %s", event.state)
        elif event.event == "speak_start":
            sm.transition(AvatarState.SPEAKING)
            if tts and event.text:
                try:
                    audio, timings = tts.synthesize(event.text)
                    audio_player.play(
                        audio,
                        sample_rate=tts.sample_rate,
                        word_timings=timings,
                        on_word=lambda wt: None,  # Could update mouth frame
                        on_complete=lambda: sm.transition(AvatarState.IDLE),
                    )
                except Exception as e:
                    log.error("TTS failed: %s", e)
        elif event.event == "speak_end":
            sm.transition(AvatarState.IDLE)

    bus.on_event = handle_event

    # Shutdown handler
    running = True

    def shutdown(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start
    bus.start()
    log.info("Avatar started. Persona: %s. Socket: %s", persona.name, args.socket)
    log.info("TTS: %s", "enabled" if tts else "disabled (animation only)")

    frame_index = 0
    last_event = ""

    try:
        with term.fullscreen(), term.hidden_cursor():
            while running:
                state = sm.state
                frame = renderer.get_current_frame(state, frame_index)
                status = renderer.format_status_bar(
                    state=state,
                    connected=connected,
                    tts_loaded=tts is not None,
                    last_event=last_event,
                )
                renderer.render_frame(frame, status)

                rate = renderer.get_frame_rate(state)
                time.sleep(rate)
                frame_index = renderer.next_frame_index(state, frame_index)

                # Check for quit key
                key = term.inkey(timeout=0)
                if key == "q" or key.name == "KEY_ESCAPE":
                    break
    finally:
        audio_player.stop()
        sm.shutdown()
        bus.stop()
        log.info("Avatar stopped.")


if __name__ == "__main__":
    main()
```

**Step 2: Verify it at least parses**

```bash
python -c "from avatar.main import main; print('OK')"
```

**Step 3: Commit**

```bash
git add src/avatar/main.py
git commit -m "feat: main entry point with persona, TTS, and renderer wiring"
```

---

### Task 13: MCP Server

**Files:**
- Create: `src/avatar/bridge/mcp_server.py`

**Step 1: Implement MCP server**

`src/avatar/bridge/mcp_server.py`:
```python
"""MCP server exposing avatar control as tools for Claude Code."""

from __future__ import annotations

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from avatar.bridge.hooks import think, respond, listen, idle, error

server = Server("ascii-avatar")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="avatar_think",
            description="Signal the avatar to enter thinking state",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="avatar_speak",
            description="Make the avatar speak text aloud with TTS and mouth animation",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to speak"},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="avatar_listen",
            description="Signal the avatar to enter listening state",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="avatar_idle",
            description="Return the avatar to idle state",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "avatar_think":
        think()
        return [TextContent(type="text", text="Avatar: thinking")]
    elif name == "avatar_speak":
        text = arguments.get("text", "")
        respond(text)
        return [TextContent(type="text", text=f"Avatar: speaking '{text}'")]
    elif name == "avatar_listen":
        listen()
        return [TextContent(type="text", text="Avatar: listening")]
    elif name == "avatar_idle":
        idle()
        return [TextContent(type="text", text="Avatar: idle")]
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def run():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    import asyncio
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

**Step 2: Verify it parses**

```bash
python -c "from avatar.bridge.mcp_server import server; print('OK')"
```

**Step 3: Commit**

```bash
git add src/avatar/bridge/mcp_server.py
git commit -m "feat: MCP server for Claude Code tool integration"
```

---

### Task 14: Scripts — install.sh, setup-hooks.sh, setup-tmux.sh, demo.sh

**Files:**
- Create: `scripts/install.sh`
- Create: `scripts/setup-hooks.sh`
- Create: `scripts/setup-tmux.sh`
- Create: `scripts/demo.sh`

**Step 1: Create scripts**

`scripts/install.sh`:
```bash
#!/bin/bash
set -euo pipefail

echo "=== ASCII Avatar — Dependency Installer ==="

# System deps
echo "[1/3] Installing system dependencies..."
if command -v dnf &>/dev/null; then
    echo "  sudo dnf install portaudio-devel"
    echo "  (Run manually — this script does not use sudo)"
elif command -v apt &>/dev/null; then
    echo "  sudo apt install portaudio19-dev"
    echo "  (Run manually — this script does not use sudo)"
fi

# Kokoro models
MODEL_DIR="$HOME/.cache/ascii-avatar/models"
mkdir -p "$MODEL_DIR"

echo "[2/3] Downloading Kokoro TTS models..."
if [ ! -f "$MODEL_DIR/kokoro-v1.0.onnx" ]; then
    echo "  Downloading kokoro-v1.0.onnx..."
    python -c "
from huggingface_hub import hf_hub_download
hf_hub_download('hexgrad/Kokoro-82M', 'kokoro-v1.0.onnx', local_dir='$MODEL_DIR')
print('  Done.')
"
else
    echo "  kokoro-v1.0.onnx already exists."
fi

if [ ! -f "$MODEL_DIR/voices-v1.0.bin" ]; then
    echo "  Downloading voices-v1.0.bin..."
    python -c "
from huggingface_hub import hf_hub_download
hf_hub_download('hexgrad/Kokoro-82M', 'voices-v1.0.bin', local_dir='$MODEL_DIR')
print('  Done.')
"
else
    echo "  voices-v1.0.bin already exists."
fi

# Python deps
echo "[3/3] Installing Python dependencies..."
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
    source .venv/bin/activate
fi
uv pip install -e ".[dev]"

echo ""
echo "=== Done! ==="
echo "Models: $MODEL_DIR"
echo "Test:   python -m avatar.main --no-voice"
```

`scripts/setup-hooks.sh`:
```bash
#!/bin/bash
set -euo pipefail

AVATAR_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Claude Code Avatar Hooks Setup ==="
echo ""
echo "Add the following to your Claude Code settings"
echo "(~/.claude/settings.json or project .claude/settings.json):"
echo ""
cat <<EOF
{
  "hooks": {
    "PreToolUse": [
      {
        "command": "cd $AVATAR_DIR && python -m avatar.bridge.cli think"
      }
    ],
    "PostToolUse": [
      {
        "command": "cd $AVATAR_DIR && python -m avatar.bridge.cli idle"
      }
    ],
    "Notification": [
      {
        "command": "cd $AVATAR_DIR && python -m avatar.bridge.cli speak \"\$CLAUDE_NOTIFICATION\""
      }
    ]
  }
}
EOF
echo ""
echo "Make sure the avatar process is running first:"
echo "  cd $AVATAR_DIR && python -m avatar.main"
```

`scripts/setup-tmux.sh`:
```bash
#!/bin/bash
set -euo pipefail

AVATAR_DIR="$(cd "$(dirname "$0")/.." && pwd)"

ALIAS_LINE="alias clauded-avatar='tmux new-session \"clauded\" \\; split-window -h -l 45 \"cd $AVATAR_DIR && python -m avatar.main\"'"

echo "=== tmux Integration Setup ==="
echo ""
echo "Adding alias to ~/.bashrc:"
echo "  $ALIAS_LINE"
echo ""

read -p "Add to ~/.bashrc? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "" >> ~/.bashrc
    echo "# ASCII Avatar for Claude Code" >> ~/.bashrc
    echo "$ALIAS_LINE" >> ~/.bashrc
    echo "Added! Run: source ~/.bashrc && clauded-avatar"
else
    echo "Skipped. Add manually if desired."
fi
```

`scripts/demo.sh`:
```bash
#!/bin/bash
set -euo pipefail

AVATAR_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$AVATAR_DIR"

if [ -d .venv ]; then
    source .venv/bin/activate
fi

CLI="python -m avatar.bridge.cli"
SOCKET="/tmp/ascii-avatar-demo.sock"

echo "=== ASCII Avatar Demo ==="
echo "Starting avatar process..."
python -m avatar.main --socket "$SOCKET" --no-voice &
AVATAR_PID=$!
sleep 1

echo "Cycling through states..."

echo "[idle] — breathing..."
sleep 2

echo "[thinking] — processing..."
$CLI --socket "$SOCKET" think
sleep 3

echo "[speaking] — responding..."
$CLI --socket "$SOCKET" speak "Hello, I am your AI assistant."
sleep 3

echo "[listening] — waiting for input..."
$CLI --socket "$SOCKET" listen
sleep 2

echo "[error] — something went wrong..."
$CLI --socket "$SOCKET" error "Demo error"
sleep 2

echo "[idle] — back to rest..."
$CLI --socket "$SOCKET" idle
sleep 2

echo "Demo complete. Shutting down..."
kill $AVATAR_PID 2>/dev/null
wait $AVATAR_PID 2>/dev/null
```

**Step 2: Make scripts executable**

```bash
chmod +x scripts/*.sh
```

**Step 3: Commit**

```bash
git add scripts/
git commit -m "feat: install, hooks setup, tmux setup, and demo scripts"
```

---

### Task 15: Integration Test

**Files:**
- Create: `tests/test_integration.py`

**Step 1: Write integration test**

`tests/test_integration.py`:
```python
"""Integration test — spawns avatar process, sends events, verifies lifecycle."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time

import pytest
import zmq


@pytest.fixture
def avatar_process(tmp_path):
    """Start the avatar process in headless-ish mode."""
    socket_path = str(tmp_path / "test-avatar.sock")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "avatar.main",
            "--socket", socket_path,
            "--no-voice",
        ],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env={**os.environ, "PYTHONPATH": "src", "TERM": "dumb"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(1)  # Let it start
    yield proc, socket_path

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def send_event(socket_path: str, event: dict) -> None:
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.connect(f"ipc://{socket_path}")
    time.sleep(0.05)
    sock.send_json(event)
    sock.close()
    ctx.term()


class TestIntegration:
    @pytest.mark.timeout(30)
    def test_full_lifecycle(self, avatar_process):
        proc, socket_path = avatar_process
        assert proc.poll() is None, "Process died on startup"

        # idle (already in idle)
        time.sleep(1)
        assert proc.poll() is None

        # thinking
        send_event(socket_path, {"event": "state_change", "state": "thinking"})
        time.sleep(1)
        assert proc.poll() is None

        # speaking (animation only, no TTS)
        send_event(socket_path, {
            "event": "speak_start",
            "state": "speaking",
            "text": "Hello, I am your AI assistant.",
        })
        time.sleep(1)
        assert proc.poll() is None

        # listening
        send_event(socket_path, {"event": "state_change", "state": "listening"})
        time.sleep(1)
        assert proc.poll() is None

        # back to idle
        send_event(socket_path, {"event": "state_change", "state": "idle"})
        time.sleep(0.5)
        assert proc.poll() is None

    @pytest.mark.timeout(15)
    def test_clean_shutdown(self, avatar_process):
        proc, socket_path = avatar_process
        assert proc.poll() is None
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        assert proc.returncode is not None
```

**Step 2: Run integration tests**

```bash
python -m pytest tests/test_integration.py -v --timeout=60
```

Note: This test requires a terminal-like environment. If running in a non-tty context, the blessed Terminal may not initialize. The implementing agent should handle this — if tests fail due to terminal issues, wrap the avatar process startup with `TERM=dumb` or add a `--headless` flag.

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration test for full avatar lifecycle"
```

---

### Task 16: README & USAGE docs

**Files:**
- Create: `README.md`
- Create: `USAGE.md`

**Step 1: Write README.md**

The implementing agent should write a README covering:
- Project description (1 paragraph)
- Architecture diagram (ASCII art from design doc)
- Quick start (3 commands: install, start avatar, test with CLI)
- Persona list
- Requirements
- License: MIT

**Step 2: Write USAGE.md**

Cover:
1. Starting the avatar: `python -m avatar.main`
2. CLI flags: `--persona`, `--no-voice`, `--socket`, `--compact`, `--audio-device`
3. Sending events via CLI: `python -m avatar.bridge.cli think`
4. Claude Code hooks setup (reference setup-hooks.sh output)
5. MCP server setup (JSON config)
6. tmux integration (reference setup-tmux.sh)
7. Troubleshooting: no audio, no model, socket errors

**Step 3: Commit**

```bash
git add README.md USAGE.md
git commit -m "docs: README and usage guide"
```

---

## Task Dependency Graph

```
Task 1 (scaffold)
├── Task 2 (state machine)
├── Task 3 (event bus)
├── Task 4 (personas)
├── Task 5 (frames)
│
├── Task 6 (renderer) ← depends on 4, 5
├── Task 7 (bridge) ← depends on 3
├── Task 8 (voice base + audio) ← independent
│   ├── Task 9 (kokoro) ← depends on 8
│   ├── Task 10 (elevenlabs) ← depends on 8
│   └── Task 11 (piper) ← depends on 8
│
├── Task 12 (main.py) ← depends on 2, 3, 4, 6, 8, 9
├── Task 13 (MCP server) ← depends on 7
├── Task 14 (scripts) ← depends on 12
├── Task 15 (integration test) ← depends on 12, 7
└── Task 16 (docs) ← depends on all
```

**Parallelizable waves:**
- Wave 1: Task 1
- Wave 2: Tasks 2, 3, 4, 5, 8 (all independent after scaffold)
- Wave 3: Tasks 6, 7, 9, 10, 11 (depend on wave 2)
- Wave 4: Tasks 12, 13 (depend on wave 3)
- Wave 5: Tasks 14, 15 (depend on wave 4)
- Wave 6: Task 16 (final)
