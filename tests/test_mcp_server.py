"""The MCP server, exercised through a real MCP client connected in-process."""

from typing import Any

import pytest
from mcp import Client

from warehouse_scout.mcp_server.server import build_server
from warehouse_scout.tools import Tool, ToolError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def echo(arguments: dict[str, Any]) -> str:
    if arguments["text"] == "fail":
        raise ToolError("ORA-00904: invalid identifier")
    return f"echo: {arguments['text']}"


ECHO = Tool(
    name="echo",
    description="Return the given text.",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    handler=echo,
)


async def test_tools_are_listed_with_their_descriptions_and_schemas() -> None:
    async with Client(build_server([ECHO])) as client:
        listed = (await client.list_tools()).tools

    assert [(tool.name, tool.description) for tool in listed] == [
        ("echo", "Return the given text.")
    ]
    assert listed[0].input_schema == ECHO.input_schema


async def test_calling_a_tool_returns_its_text() -> None:
    async with Client(build_server([ECHO])) as client:
        result = await client.call_tool("echo", {"text": "hello"})

    assert result.is_error is False
    assert [block.text for block in result.content if block.type == "text"] == ["echo: hello"]


async def test_tool_failure_is_returned_as_an_error_result_the_client_can_read() -> None:
    async with Client(build_server([ECHO])) as client:
        result = await client.call_tool("echo", {"text": "fail"})

    assert result.is_error is True
    assert result.content[0].type == "text"
    assert result.content[0].text == "ORA-00904: invalid identifier"


async def test_unknown_tool_is_an_error_result_not_a_crash() -> None:
    async with Client(build_server([ECHO])) as client:
        result = await client.call_tool("drop_everything", {})
        still_alive = await client.call_tool("echo", {"text": "after"})

    assert result.is_error is True
    assert still_alive.is_error is False


async def test_server_identifies_itself_and_explains_its_tools() -> None:
    async with Client(build_server([ECHO])) as client:
        assert client.server_info is not None
        assert client.server_info.name == "warehouse-scout"
        assert "read-only" in (client.instructions or "")
