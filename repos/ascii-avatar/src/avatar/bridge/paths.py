"""Shared paths for hook scripts — secure defaults.

All runtime files (socket, logs, throttle) go to a user-private
directory rather than world-readable /tmp.

Precedence for socket path:
  1. AVATAR_SOCKET env var
  2. $XDG_RUNTIME_DIR/ascii-avatar.sock
  3. ~/.local/share/ascii-avatar/ascii-avatar.sock

Precedence for data dir (logs, throttle):
  1. $XDG_RUNTIME_DIR/ascii-avatar/
  2. ~/.local/share/ascii-avatar/
"""

from __future__ import annotations

import os
from pathlib import Path


def _runtime_dir() -> Path:
    """User-private runtime directory."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        d = Path(xdg) / "ascii-avatar"
    else:
        d = Path.home() / ".local" / "share" / "ascii-avatar"
    d.mkdir(parents=True, exist_ok=True)
    # Ensure directory is user-private
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def get_socket_path() -> str:
    """Resolve the ZeroMQ socket path."""
    env = os.environ.get("AVATAR_SOCKET")
    if env:
        import logging
        logging.getLogger(__name__).warning(
            "Using non-default socket path from AVATAR_SOCKET: %s", env
        )
        return env
    return str(_runtime_dir() / "ascii-avatar.sock")


def get_log_path() -> Path:
    """Resolve the hook log file path (user-private)."""
    log = _runtime_dir() / "hooks.log"
    # Ensure log file is user-private on creation
    if not log.exists():
        log.touch(mode=0o600)
    return log


def get_throttle_path() -> Path:
    """Resolve the tool-speech throttle file path."""
    return _runtime_dir() / "last-tool-speech"
