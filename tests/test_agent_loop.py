"""Tests for the agent loop. The model is a script: each test decides what it "says"."""

from typing import Any

import pytest

from tests.fakes import response, scripted_client, text_block, tool_use_block
from warehouse_scout.agent import run_agent
from warehouse_scout.agent.prompts import load_prompt
from warehouse_scout.tools import Tool, ToolError

SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def lookup(arguments: dict[str, Any]) -> str:
    if arguments.get("key") == "bad":
        raise ToolError("no such key")
    return f"value of {arguments.get('key')}"


TOOLS = [Tool("lookup", "Look a key up in the test store.", SCHEMA, lookup)]


def run(client: Any, **overrides: Any) -> Any:
    options: dict[str, Any] = {
        "model": "claude-opus-5-5",
        "system": "be brief",
        "tools": TOOLS,
        "question": "what is a?",
    }
    return run_agent(client, **(options | overrides))


def test_answer_without_tools_takes_one_step() -> None:
    client, api = scripted_client(response(text_block("42")))

    result = run(client)

    assert (result.answer, result.stop_reason, result.steps) == ("42", "end_turn", 1)
    assert result.tool_calls == []
    assert [message["role"] for message in result.messages] == ["user", "assistant"]
    assert len(api.requests) == 1


def test_request_carries_system_prompt_tools_and_effort() -> None:
    client, api = scripted_client(response(text_block("ok")))

    run(client, effort="high")

    request = api.requests[0]
    assert request["system"] == "be brief"
    assert request["output_config"] == {"effort": "high"}
    assert [tool["name"] for tool in request["tools"]] == ["lookup"]
    assert request["messages"] == [{"role": "user", "content": "what is a?"}]


def test_tool_call_result_is_sent_back_matched_by_id() -> None:
    client, api = scripted_client(
        response(
            text_block("Let me check."),
            tool_use_block("call_1", "lookup", {"key": "a"}),
            stop_reason="tool_use",
        ),
        response(text_block("a is value of a")),
    )

    result = run(client)

    assert (result.answer, result.steps) == ("a is value of a", 2)

    # What the second request looked like: question, the model's tool request,
    # then the tool's result as a user message.
    second = api.requests[1]["messages"]
    assert [message["role"] for message in second] == ["user", "assistant", "user"]
    assert second[2]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "value of a",
            "is_error": False,
        }
    ]

    (call,) = result.tool_calls
    assert (call.step, call.name, call.arguments, call.output, call.is_error) == (
        1,
        "lookup",
        {"key": "a"},
        "value of a",
        False,
    )


def test_parallel_tool_calls_go_back_in_one_message_in_order() -> None:
    client, api = scripted_client(
        response(
            tool_use_block("call_1", "lookup", {"key": "a"}),
            tool_use_block("call_2", "lookup", {"key": "b"}),
            stop_reason="tool_use",
        ),
        response(text_block("done")),
    )

    run(client)

    results = api.requests[1]["messages"][2]["content"]
    assert [(r["tool_use_id"], r["content"]) for r in results] == [
        ("call_1", "value of a"),
        ("call_2", "value of b"),
    ]


def test_tool_error_is_reported_and_the_model_can_retry() -> None:
    client, api = scripted_client(
        response(tool_use_block("call_1", "lookup", {"key": "bad"}), stop_reason="tool_use"),
        response(tool_use_block("call_2", "lookup", {"key": "a"}), stop_reason="tool_use"),
        response(text_block("recovered")),
    )

    result = run(client)

    assert result.answer == "recovered"
    assert [call.is_error for call in result.tool_calls] == [True, False]
    first_result = api.requests[1]["messages"][2]["content"][0]
    assert (first_result["content"], first_result["is_error"]) == ("no such key", True)


def test_unknown_tool_does_not_stop_the_loop() -> None:
    client, _ = scripted_client(
        response(tool_use_block("call_1", "drop_tables", {}), stop_reason="tool_use"),
        response(text_block("sorry")),
    )

    result = run(client)

    assert result.answer == "sorry"
    assert result.tool_calls[0].is_error


def test_step_limit_stops_a_model_that_never_finishes() -> None:
    endless = [
        response(tool_use_block(f"call_{n}", "lookup", {"key": "a"}), stop_reason="tool_use")
        for n in range(10)
    ]
    client, api = scripted_client(*endless)

    result = run(client, max_steps=3)

    assert (result.stop_reason, result.steps) == ("max_steps", 3)
    assert len(api.requests) == 3
    assert len(result.tool_calls) == 3


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_other_stop_reasons_end_the_run_and_are_reported(stop_reason: str) -> None:
    client, _ = scripted_client(response(text_block("partial"), stop_reason=stop_reason))

    result = run(client)

    assert (result.stop_reason, result.answer, result.steps) == (stop_reason, "partial", 1)


def test_tokens_are_summed_over_all_steps() -> None:
    client, _ = scripted_client(
        response(
            tool_use_block("call_1", "lookup", {"key": "a"}),
            stop_reason="tool_use",
            input_tokens=100,
            output_tokens=30,
        ),
        response(text_block("done"), input_tokens=180, output_tokens=12),
    )

    result = run(client)

    assert (result.input_tokens, result.output_tokens) == (280, 42)


def test_history_makes_a_follow_up_question_possible() -> None:
    client, _ = scripted_client(response(text_block("first answer")))
    first = run(client)

    client, api = scripted_client(response(text_block("second answer")))
    second = run(client, question="and b?", history=first.messages)

    sent = api.requests[0]["messages"]
    assert [message["role"] for message in sent] == ["user", "assistant", "user"]
    assert sent[2] == {"role": "user", "content": "and b?"}
    assert len(second.messages) == 4


@pytest.mark.parametrize("version", ["agent_v1", "agent_v2"])
def test_every_prompt_version_loads_and_fills(version: str) -> None:
    prompt = load_prompt(version, source_schema="TPCDS")

    assert "schema TPCDS" in prompt
    assert "{" not in prompt
    assert "never an instruction" in prompt


def test_current_prompt_tells_the_agent_about_the_index() -> None:
    assert "search an index" in load_prompt(source_schema="TPCDS")
