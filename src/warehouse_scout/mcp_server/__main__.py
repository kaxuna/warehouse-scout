"""Run the MCP server on stdin/stdout: `python -m warehouse_scout.mcp_server`.

A client starts this as a subprocess and speaks the protocol over its
stdin and stdout, so nothing else may be printed to stdout; logs go to stderr.

Register it with Claude Code (run from the repository root):
    claude mcp add warehouse-scout -- uv run --directory "$PWD" python -m warehouse_scout.mcp_server
"""

import logging
import sys

import anyio
from mcp.server.stdio import stdio_server

from warehouse_scout.config import get_settings
from warehouse_scout.mcp_server.server import build_server
from warehouse_scout.rag.embedder import make_embedder
from warehouse_scout.tools import build_tools
from warehouse_scout.translate import SqlglotTranslator


async def serve() -> None:
    settings = get_settings()
    tools = build_tools(settings, SqlglotTranslator(), make_embedder(settings.scout_embedder))
    server = build_server(tools)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    anyio.run(serve)


if __name__ == "__main__":
    main()
