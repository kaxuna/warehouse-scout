"""Stand-ins for the Claude API, shared by tests. No network, no key, no cost."""

import copy
from types import SimpleNamespace
from typing import Any, cast

import anthropic


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_use_block(block_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=arguments)


def response(
    *blocks: SimpleNamespace,
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 20,
    model: str = "claude-opus-5-5",
) -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        _request_id="req_test",
    )


class ScriptedMessages:
    """Plays back a fixed list of responses, one per call, and records each request."""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        # Deep copy: the agent keeps appending to the same `messages` list after
        # this call, and the test wants to see it as it was when it was sent.
        self.requests.append(copy.deepcopy(kwargs))
        if not self._responses:
            raise AssertionError("The agent made more API calls than the test scripted.")
        return self._responses.pop(0)


def scripted_client(*responses: SimpleNamespace) -> tuple[anthropic.Anthropic, ScriptedMessages]:
    messages = ScriptedMessages(list(responses))
    return cast(anthropic.Anthropic, SimpleNamespace(messages=messages)), messages
