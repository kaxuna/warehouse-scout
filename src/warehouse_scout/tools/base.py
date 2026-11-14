"""What a tool is, and how one is executed without ever crashing the agent.

A tool is three things the model sees (name, description, input schema) and
one thing it does not (the Python function that runs). The model reads the
description to decide when to call the tool, so the description is prompt
text and deserves the same care.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from anthropic.types import ToolParam

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """An expected failure the model should read and react to (bad SQL, timeout)."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]

    def to_api_param(self) -> ToolParam:
        """The tool as the API wants it. `strict` makes the API guarantee that
        the arguments match the schema, so handlers need not re-validate them."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


@dataclass(frozen=True)
class ToolOutcome:
    """What goes back to the model: text, and whether it describes a failure."""

    content: str
    is_error: bool = False


def execute_tool(tools: Mapping[str, Tool], name: str, arguments: dict[str, Any]) -> ToolOutcome:
    """Run one tool call. Never raises: every failure becomes an error outcome.

    The model can recover from an error it can read (fix the SQL, pick another
    tool). It cannot recover from an exception that ends the run.
    """
    tool = tools.get(name)
    if tool is None:
        known = ", ".join(sorted(tools))
        return ToolOutcome(f"Unknown tool {name!r}. Available tools: {known}.", is_error=True)

    try:
        return ToolOutcome(tool.handler(arguments))
    except ToolError as error:
        return ToolOutcome(str(error), is_error=True)
    except Exception as error:
        # Deliberately broad: a bug in a tool must not take the agent down.
        # It is logged with its traceback so the bug is still visible to us.
        logger.exception("Tool %s crashed", name)
        return ToolOutcome(f"Tool {name} failed: {type(error).__name__}: {error}", is_error=True)
