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
