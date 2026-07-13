"""Argparse CLI entry point for the avatar bridge."""

import argparse
import sys

from avatar.bridge import hooks
from avatar.bridge.paths import get_socket_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="avatar-bridge",
        description="Send events to the ASCII avatar process",
    )
    parser.add_argument(
        "--socket", default=get_socket_path(),
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
