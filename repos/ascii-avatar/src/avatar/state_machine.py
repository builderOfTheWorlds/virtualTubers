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
