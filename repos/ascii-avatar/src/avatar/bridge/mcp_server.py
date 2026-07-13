"""MCP server exposing avatar control as tools for Claude Code."""

from __future__ import annotations

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from avatar.bridge.hooks import think, respond, listen, idle
from avatar.bridge.paths import get_socket_path

SOCKET_PATH = get_socket_path()

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
        think(socket_path=SOCKET_PATH)
        return [TextContent(type="text", text="Avatar: thinking")]
    elif name == "avatar_speak":
        text = arguments.get("text", "")
        respond(text, socket_path=SOCKET_PATH)
        return [TextContent(type="text", text=f"Avatar: speaking '{text}'")]
    elif name == "avatar_listen":
        listen(socket_path=SOCKET_PATH)
        return [TextContent(type="text", text="Avatar: listening")]
    elif name == "avatar_idle":
        idle(socket_path=SOCKET_PATH)
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
