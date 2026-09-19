"""MCP server over stdio.

Opens the built SQLite index read-only. There is no write path from the server
and no network access; a tool call is a query, nothing more.
"""

from __future__ import annotations

import json
import sys

from ..config import default_paths
from ..store import sqlite as store
from . import tools


def main() -> None:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError:
        print(
            "The MCP server needs the `mcp` extra: pip install 'sgcorpus[mcp]'",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    paths = default_paths()
    if not paths.db.exists():
        print(
            f"No index at {paths.db}. Build one first:\n"
            "  sgcorpus fetch hansard --start 2024-01-01\n"
            "  sgcorpus normalise hansard\n"
            "  sgcorpus index",
            file=sys.stderr,
        )
        raise SystemExit(1)

    conn = store.connect(paths.db, read_only=True)
    server = Server("sg-legal-corpus")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["inputSchema"],
            )
            for spec in tools.TOOL_SPECS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        result = tools.handle(conn, name, arguments or {})
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    import asyncio

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()
