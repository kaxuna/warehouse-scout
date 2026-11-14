"""The agent behind the Translator interface, with a scripted model."""

from types import SimpleNamespace
from typing import Any, cast

import anthropic
import pytest

from tests.fakes import response, scripted_client, text_block, tool_use_block
from warehouse_scout.agent.prompts import AGENT_PROMPT_VERSION
from warehouse_scout.agent.translator import AgentTranslator, extract_sql
from warehouse_scout.evals.translation import (
    SYSTEMS,
    CaseResult,
    MissingCredentialsError,
    ReportsUsage,
    history_record,
)
from warehouse_scout.tools import Tool
from warehouse_scout.translate import SqlglotTranslator, Translator

SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
TOOLS = [Tool("noop", "Does nothing; exists so the agent has a tool.", SCHEMA, lambda a: "ok")]


def agent(client: anthropic.Anthropic) -> AgentTranslator:
    return AgentTranslator(client=client, model="claude-opus-5-5", system="s", tools=TOOLS)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Here:\n```sql\nSELECT 1\n```", "SELECT 1"),
        ("```SQL\nSELECT 1;\n```\nVerified.", "SELECT 1"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("Original:\n```sql\nSELECT a\n```\nTranslated:\n```sql\nSELECT b\n```", "SELECT b"),
        ("```sql\nSELECT a,\n       b\nFROM t\n```", "SELECT a,\n       b\nFROM t"),
        ("No code here, sorry.", None),
        ("```sql\n\n```", None),
    ],
)
def test_extract_sql(answer: str, expected: str | None) -> None:
    assert extract_sql(answer) == expected


def test_agent_satisfies_both_protocols() -> None:
    client, _ = scripted_client()
    translator: Translator = agent(client)

    assert translator.name == "agent"
    assert isinstance(translator, ReportsUsage)
    assert not isinstance(SqlglotTranslator(), ReportsUsage)


def test_translate_asks_the_agent_and_returns_the_sql_from_its_answer() -> None:
    client, api = scripted_client(
        response(tool_use_block("c1", "noop", {}), stop_reason="tool_use"),
        response(text_block("Verified.\n```sql\nSELECT COALESCE(a, 0) FROM t\n```")),
    )

    translation = agent(client).translate("SELECT NVL(a, 0) FROM t", "oracle", "postgres")

    assert translation.ok
    assert translation.sql == "SELECT COALESCE(a, 0) FROM t"
    question = api.requests[0]["messages"][0]["content"]
    assert "oracle query to postgres" in question
    assert "SELECT NVL(a, 0) FROM t" in question


def test_answer_without_sql_is_a_translation_error() -> None:
    client, _ = scripted_client(
        response(text_block("I could not do it."), stop_reason="max_tokens")
    )

    translation = agent(client).translate("SELECT 1 FROM dual", "oracle", "postgres")

    assert translation.sql is None
    assert translation.error == "No SQL block in the answer (stop reason: max_tokens)"


def test_api_failure_is_a_translation_error_not_a_crash() -> None:
    class BrokenMessages:
        def create(self, **kwargs: Any) -> Any:
            raise anthropic.AnthropicError("connection lost")

    client = cast(anthropic.Anthropic, SimpleNamespace(messages=BrokenMessages()))

    translation = agent(client).translate("SELECT 1 FROM dual", "oracle", "postgres")

    assert translation.error == "AnthropicError: connection lost"


def test_usage_summary_totals_all_runs() -> None:
    client, _ = scripted_client(
        response(text_block("```sql\nSELECT 1\n```"), input_tokens=1000, output_tokens=200),
        response(tool_use_block("c1", "noop", {}), stop_reason="tool_use", input_tokens=500),
        response(text_block("```sql\nSELECT 2\n```"), input_tokens=700, output_tokens=100),
    )
    translator = agent(client)

    translator.translate("SELECT 1 FROM dual", "oracle", "postgres")
    translator.translate("SELECT 2 FROM dual", "oracle", "postgres")

    summary = translator.usage_summary()
    assert (summary["input_tokens"], summary["output_tokens"]) == (2200, 320)
    assert summary["tool_calls"] == 1
    # (2200 * $4 + 320 * $20) per million tokens, over 2 cases
    assert summary["cost_usd"] == pytest.approx(0.0152)
    assert summary["cost_per_case_usd"] == pytest.approx(0.0076)
    assert summary["prompt_version"] == AGENT_PROMPT_VERSION


def test_history_record_includes_usage() -> None:
    results = [CaseResult("a", "match", "same rows", 1, 1)]

    record = history_record("agent", results, {"cost_usd": 0.5, "model": "claude-opus-5-5"})

    assert (record["system"], record["passed"], record["cost_usd"]) == ("agent", 1, 0.5)


def test_agent_system_refuses_to_start_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    settings = cast(Any, SimpleNamespace(anthropic_api_key=None))

    with pytest.raises(MissingCredentialsError, match="ANTHROPIC_API_KEY"):
        SYSTEMS["agent"](settings)
