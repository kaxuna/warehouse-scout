"""Tests for the first Claude call. The API is replaced by a stand-in: no network, no key."""

from types import SimpleNamespace
from typing import Any, cast

import anthropic
import pytest

from warehouse_scout.llm.hello import (
    EFFORT,
    MAX_TOKENS,
    CallResult,
    ask,
    estimate_cost_usd,
    format_report,
    has_credentials,
)


class FakeMessages:
    """Stands in for `client.messages`: records the request, returns a canned reply."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.request: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.request = kwargs
        return self.response


def fake_client(response: Any) -> tuple[anthropic.Anthropic, FakeMessages]:
    messages = FakeMessages(response)
    # cast() tells the type checker "treat this as an Anthropic client". Python
    # itself only needs the object to have a `.messages.create` (duck typing).
    return cast(anthropic.Anthropic, SimpleNamespace(messages=messages)), messages


def canned_response(*blocks: Any, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        model="claude-opus-5-5",
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=120, output_tokens=80),
        _request_id="req_test",
    )


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def test_ask_sends_the_expected_request() -> None:
    client, messages = fake_client(canned_response(text_block("ok")))

    ask(client, "claude-opus-5-5", "be brief", "translate this")

    assert messages.request == {
        "model": "claude-opus-5-5",
        "max_tokens": MAX_TOKENS,
        "system": "be brief",
        "output_config": {"effort": EFFORT},
        "messages": [{"role": "user", "content": "translate this"}],
    }


def test_ask_joins_text_blocks_and_skips_the_rest() -> None:
    thinking = SimpleNamespace(type="thinking", thinking="")
    client, _ = fake_client(canned_response(thinking, text_block("SELECT 1"), text_block(";")))

    result = ask(client, "claude-opus-5-5", "system", "question")

    assert result.text == "SELECT 1;"


def test_ask_reports_usage_and_metadata() -> None:
    client, _ = fake_client(canned_response(text_block("ok"), stop_reason="max_tokens"))

    result = ask(client, "claude-opus-5-5", "system", "question")

    assert (result.input_tokens, result.output_tokens) == (120, 80)
    assert result.stop_reason == "max_tokens"
    assert result.request_id == "req_test"
    assert result.latency_seconds >= 0


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-opus-5-5", (1_000_000 * 4 + 500_000 * 20) / 1_000_000),
        ("claude-haiku-5-5", (1_000_000 * 0.10 + 500_000 * 0.50) / 1_000_000),
    ],
)
def test_estimate_cost(model: str, expected: float) -> None:
    assert estimate_cost_usd(model, 1_000_000, 500_000) == pytest.approx(expected)


def test_estimate_cost_is_none_for_an_unknown_model() -> None:
    assert estimate_cost_usd("some-future-model", 1000, 1000) is None


def test_format_report_shows_cost_or_says_it_is_unknown() -> None:
    known = CallResult("x", "claude-opus-5-5", "end_turn", 1000, 500, 2.34, "req_1")
    unknown = CallResult("x", "some-future-model", "end_turn", 1000, 500, 2.34, "req_2")

    assert "$0.01400" in format_report(known)
    assert "2.3 s" in format_report(known)
    assert "unknown" in format_report(unknown)


def test_has_credentials() -> None:
    assert has_credentials(anthropic.Anthropic(api_key="test-key"))
    assert not has_credentials(
        cast(anthropic.Anthropic, SimpleNamespace(api_key=None, auth_token=None, credentials=None))
    )
