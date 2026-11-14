"""Tests for tool definitions and tool execution. Databases are replaced by stubs."""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from warehouse_scout.tools import Tool, ToolError, build_tools, execute_tool, warehouse_tools
from warehouse_scout.tools.warehouse_tools import MODEL_MAX_ROWS, format_result
from warehouse_scout.translate import Translation
from warehouse_scout.warehouse import QueryResult


def make_tool(handler: Any, name: str = "echo") -> Tool:
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    return Tool(name=name, description="test tool", input_schema=schema, handler=handler)


# --- execute_tool: every failure becomes something the model can read -------------


def test_successful_tool_returns_its_output() -> None:
    tools = {"echo": make_tool(lambda arguments: f"got {arguments['x']}")}

    outcome = execute_tool(tools, "echo", {"x": 1})

    assert (outcome.content, outcome.is_error) == ("got 1", False)


def test_unknown_tool_is_an_error_that_lists_the_real_tools() -> None:
    tools = {"echo": make_tool(lambda arguments: "x")}

    outcome = execute_tool(tools, "delete_everything", {})

    assert outcome.is_error
    assert "delete_everything" in outcome.content
    assert "echo" in outcome.content


def test_tool_error_message_is_passed_to_the_model() -> None:
    def failing(arguments: dict[str, Any]) -> str:
        raise ToolError("ORA-00904: invalid identifier")

    outcome = execute_tool({"echo": make_tool(failing)}, "echo", {})

    assert (outcome.content, outcome.is_error) == ("ORA-00904: invalid identifier", True)


def test_a_crashing_tool_does_not_raise() -> None:
    def buggy(arguments: dict[str, Any]) -> str:
        return str(arguments["missing_key"])  # KeyError: a bug, not an expected failure

    outcome = execute_tool({"echo": make_tool(buggy)}, "echo", {})

    assert outcome.is_error
    assert "KeyError" in outcome.content


# --- Tool definitions ----------------------------------------------------------------


class StubTranslator:
    name = "stub"

    def __init__(self, translation: Translation) -> None:
        self.translation = translation

    def translate(self, sql: str, source: str, target: str) -> Translation:
        return self.translation


def tools_by_name(translation: Translation | None = None) -> dict[str, Tool]:
    translator = StubTranslator(translation or Translation(sql="SELECT 1"))
    # settings is only handed on to run_readonly, which these tests replace.
    return {tool.name: tool for tool in build_tools(None, translator)}  # type: ignore[arg-type]


def test_three_tools_with_strict_schemas() -> None:
    tools = tools_by_name()

    assert set(tools) == {"run_readonly_sql", "translate_sql", "compare_query_results"}
    for tool in tools.values():
        assert tool.to_api_param()["strict"] is True
        assert tool.input_schema["additionalProperties"] is False
        assert set(tool.input_schema["required"]) == set(tool.input_schema["properties"])
        assert len(tool.description) > 80  # a description the model can act on


def test_format_result_is_json_with_exact_numbers_and_dates() -> None:
    result = QueryResult(
        columns=("price", "sold"),
        rows=[(Decimal("0.10"), datetime(2024, 3, 5, 13, 45))],
        truncated=True,
    )

    assert json.loads(format_result(result)) == {
        "columns": ["price", "sold"],
        "rows": [["0.10", "2024-03-05 13:45:00"]],
        "row_count": 1,
        "truncated": True,
    }


def test_run_readonly_sql_limits_rows_and_passes_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run_readonly(settings: Any, database: str, sql: str, **limits: Any) -> QueryResult:
        seen.update(database=database, sql=sql, **limits)
        return QueryResult(columns=("n",), rows=[(3,)])

    monkeypatch.setattr(warehouse_tools, "run_readonly", fake_run_readonly)

    outcome = execute_tool(
        tools_by_name(), "run_readonly_sql", {"database": "target", "sql": "SELECT 3"}
    )

    assert json.loads(outcome.content)["rows"] == [[3]]
    assert seen == {"database": "target", "sql": "SELECT 3", "max_rows": MODEL_MAX_ROWS}


def test_run_readonly_sql_reports_database_errors_as_tool_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = QueryResult(error='column "nope" does not exist', error_code="42703")
    monkeypatch.setattr(warehouse_tools, "run_readonly", lambda *args, **kwargs: failed)

    outcome = execute_tool(
        tools_by_name(), "run_readonly_sql", {"database": "target", "sql": "SELECT nope"}
    )

    assert (outcome.content, outcome.is_error) == ('42703: column "nope" does not exist', True)


def test_translate_sql_marks_its_output_unverified() -> None:
    tools = tools_by_name(Translation(sql="SELECT COALESCE(a, 0)", warnings=("careful",)))
    arguments = {
        "sql": "SELECT NVL(a, 0)",
        "source_dialect": "oracle",
        "target_dialect": "postgres",
    }

    outcome = execute_tool(tools, "translate_sql", arguments)

    assert json.loads(outcome.content) == {
        "translator": "stub",
        "sql": "SELECT COALESCE(a, 0)",
        "warnings": ["careful"],
        "verified": False,
    }


def test_translate_sql_failure_is_a_tool_error() -> None:
    tools = tools_by_name(Translation(sql=None, error="ParseError: nope"))
    arguments = {"sql": "SELEC", "source_dialect": "oracle", "target_dialect": "postgres"}

    outcome = execute_tool(tools, "translate_sql", arguments)

    assert (outcome.content, outcome.is_error) == ("ParseError: nope", True)


def test_compare_query_results_runs_each_query_on_its_own_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_run_readonly(settings: Any, database: str, sql: str, **limits: Any) -> QueryResult:
        calls.append((database, sql))
        rows = [(1,), (2,)] if database == "source" else [(2,), (1,)]
        return QueryResult(columns=("n",), rows=rows)

    monkeypatch.setattr(warehouse_tools, "run_readonly", fake_run_readonly)

    outcome = execute_tool(
        tools_by_name(),
        "compare_query_results",
        {"source_sql": "SELECT o FROM dual", "target_sql": "SELECT p"},
    )

    assert calls == [("source", "SELECT o FROM dual"), ("target", "SELECT p")]
    assert json.loads(outcome.content) == {
        "status": "match",
        "detail": "same rows",
        "source_rows": 2,
        "target_rows": 2,
    }
