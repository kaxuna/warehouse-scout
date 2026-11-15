"""The MCP server with the real tools: in-process, and as a subprocess over stdio.

Needs `make setup-data` first.
"""

import json
import sys

import pytest
from mcp import Client, StdioServerParameters

from warehouse_scout.config import get_settings
from warehouse_scout.mcp_server.server import build_server
from warehouse_scout.rag.embedder import HashEmbedder
from warehouse_scout.tools import build_tools
from warehouse_scout.translate import SqlglotTranslator

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

EXPECTED_TOOLS = {"search_schema", "run_readonly_sql", "translate_sql", "compare_query_results"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def text_of(result: object) -> str:
    (block,) = result.content  # type: ignore[attr-defined]
    return str(block.text)


async def test_real_tools_through_an_in_process_client() -> None:
    tools = build_tools(get_settings(), SqlglotTranslator(), HashEmbedder())

    async with Client(build_server(tools)) as client:
        listed = {tool.name for tool in (await client.list_tools()).tools}
        rows = await client.call_tool(
            "run_readonly_sql", {"database": "target", "sql": "SELECT COUNT(*) AS n FROM item"}
        )
        refused = await client.call_tool(
            "run_readonly_sql", {"database": "source", "sql": "DELETE FROM item"}
        )
        draft = await client.call_tool(
            "translate_sql",
            {
                "sql": "SELECT NVL(a, 0) FROM t",
                "source_dialect": "oracle",
                "target_dialect": "postgres",
            },
        )

    assert listed == EXPECTED_TOOLS
    assert json.loads(text_of(rows))["rows"][0][0] > 0
    assert refused.is_error is True  # the read-only guardrail holds through MCP too
    assert "ORA-" in text_of(refused)
    assert json.loads(text_of(draft))["sql"] == "SELECT COALESCE(a, 0) FROM t"


async def test_server_as_a_subprocess_over_stdio() -> None:
    # Exactly how Claude Desktop or Claude Code would start it.
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "warehouse_scout.mcp_server"],
        env={"SCOUT_EMBEDDER": "hash", "PATH": ""},
    )

    async with Client(server) as client:
        listed = {tool.name for tool in (await client.list_tools()).tools}
        result = await client.call_tool(
            "compare_query_results",
            {
                "source_sql": "SELECT COUNT(*) FROM item WHERE ROWNUM <= 1000000",
                "target_sql": "SELECT COUNT(*) FROM item",
            },
        )

    assert listed == EXPECTED_TOOLS
    assert json.loads(text_of(result))["status"] == "match"
