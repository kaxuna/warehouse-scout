"""The agent loop, written out by hand.

    1. send the conversation and the tool definitions to the model
    2. if the model asks for tools: run them, append the results, go to 1
    3. otherwise the model has answered: stop

The API is stateless, so "conversation state" is one list, `messages`, that
this loop owns and sends in full on every step. Each step appends two
entries: what the model said (including its tool requests) and what the
tools returned.

Three ways the loop ends, all reported in `AgentResult.stop_reason`:
    end_turn                  the model finished its answer
    max_steps                 the step budget ran out first
    max_tokens / refusal /... the API ended the turn for another reason
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import anthropic
from anthropic.types import Message, MessageParam, ToolResultBlockParam

from warehouse_scout.tools import Tool, execute_tool

Effort = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_MAX_STEPS = 12
MAX_TOKENS = 16_000


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation, kept for inspection, logging and evals."""

    step: int
    name: str
    arguments: dict[str, Any]
    output: str
    is_error: bool


@dataclass
class AgentResult:
    answer: str
    stop_reason: str
    steps: int
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The full conversation. Pass it back as `history` to ask a follow-up.
    messages: list[MessageParam] = field(default_factory=list)


def response_text(response: Message) -> str:
    """The visible text of a response: its text blocks, joined."""
    return "".join(block.text for block in response.content if block.type == "text")


def run_agent(
    client: anthropic.Anthropic,
    *,
    model: str,
    system: str,
    tools: Sequence[Tool],
    question: str,
    effort: Effort = "medium",
    max_steps: int = DEFAULT_MAX_STEPS,
    history: Sequence[MessageParam] = (),
) -> AgentResult:
    tools_by_name = {tool.name: tool for tool in tools}
    tool_params = [tool.to_api_param() for tool in tools]

    messages: list[MessageParam] = [*history, {"role": "user", "content": question}]
    result = AgentResult(answer="", stop_reason="max_steps", steps=0, model=model)

    for step in range(1, max_steps + 1):
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tool_params,
            output_config={"effort": effort},
            messages=messages,
        )
        result.steps = step
        result.model = response.model
        result.input_tokens += response.usage.input_tokens
        result.output_tokens += response.usage.output_tokens

        # Append the whole response, not only its text: the tool requests (and
        # the model's thinking blocks) must be sent back unchanged next step.
        messages.append({"role": "assistant", "content": response.content})
        result.answer = response_text(response)

        if response.stop_reason != "tool_use":
            result.stop_reason = response.stop_reason or "unknown"
            break

        # The model may ask for several tools at once. Every request needs a
        # result, matched by id, and all results go back in one user message.
        tool_results: list[ToolResultBlockParam] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            outcome = execute_tool(tools_by_name, block.name, block.input)
            result.tool_calls.append(
                ToolCall(step, block.name, block.input, outcome.content, outcome.is_error)
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome.content,
                    "is_error": outcome.is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})
    # A `for` loop that ends without `break` falls through to here with
    # stop_reason still "max_steps": the budget ran out mid-task.

    result.messages = messages
    return result
