"""The real tools against the real databases, without a model.

Needs `make load`, `make noise` and `make copy-target` first.
"""

import json

import pytest

from warehouse_scout.config import get_settings
from warehouse_scout.tools import Tool, build_tools, execute_tool
from warehouse_scout.translate import SqlglotTranslator

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def tools() -> dict[str, Tool]:
    return {tool.name: tool for tool in build_tools(get_settings(), SqlglotTranslator())}


def test_schema_can_be_explored_through_the_oracle_catalog(tools: dict[str, Tool]) -> None:
    owner = get_settings().oracle_user.upper()
    sql = (
        f"SELECT table_name, comments FROM all_tab_comments "
        f"WHERE owner = '{owner}' AND table_name = 'STORE_SALES'"
    )

    outcome = execute_tool(tools, "run_readonly_sql", {"database": "source", "sql": sql})

    assert not outcome.is_error
    (row,) = json.loads(outcome.content)["rows"]
    assert row[0] == "STORE_SALES"
    assert "physical store" in row[1]


def test_function_source_is_readable_so_its_logic_can_be_inlined(tools: dict[str, Tool]) -> None:
    owner = get_settings().oracle_user.upper()
    sql = f"SELECT text FROM all_source WHERE owner = '{owner}' AND name = 'GET_QTR'"

    outcome = execute_tool(tools, "run_readonly_sql", {"database": "source", "sql": sql})

    assert "EXTRACT(MONTH FROM d)" in outcome.content


def test_large_results_are_cut_to_the_model_row_limit(tools: dict[str, Tool]) -> None:
    outcome = execute_tool(
        tools, "run_readonly_sql", {"database": "target", "sql": "SELECT * FROM item"}
    )

    payload = json.loads(outcome.content)
    assert (payload["row_count"], payload["truncated"]) == (50, True)


def test_bad_sql_comes_back_as_a_readable_error(tools: dict[str, Tool]) -> None:
    outcome = execute_tool(
        tools, "run_readonly_sql", {"database": "target", "sql": "SELECT ROWNUM FROM item"}
    )

    assert outcome.is_error
    assert outcome.content == '42703: column "rownum" does not exist'


def compare(tools: dict[str, Tool], source_sql: str, target_sql: str) -> str:
    arguments = {"source_sql": source_sql, "target_sql": target_sql}
    status: str = json.loads(execute_tool(tools, "compare_query_results", arguments).content)[
        "status"
    ]
    return status


def test_compare_confirms_a_correct_hand_translation(tools: dict[str, Tool]) -> None:
    status = compare(
        tools,
        "SELECT i_category, COUNT(*) FROM item WHERE ROWNUM <= 100000 GROUP BY i_category",
        "SELECT i_category, COUNT(*) FROM item GROUP BY i_category",
    )

    assert status == "match"


def test_compare_catches_the_silent_outer_join_bug(tools: dict[str, Tool]) -> None:
    # What sqlglot emits for a (+) join: it runs, and drops every unmatched row.
    oracle_outer_join = (
        "SELECT COUNT(*) FROM store_sales ss, store_returns sr "
        "WHERE ss.ss_ticket_number = sr.sr_ticket_number(+) AND ss.ss_item_sk = sr.sr_item_sk(+)"
    )
    wrong_inner_join = oracle_outer_join.replace("(+)", "")
    right_left_join = (
        "SELECT COUNT(*) FROM store_sales ss LEFT JOIN store_returns sr "
        "ON ss.ss_ticket_number = sr.sr_ticket_number AND ss.ss_item_sk = sr.sr_item_sk"
    )

    assert compare(tools, oracle_outer_join, wrong_inner_join) == "mismatch"
    assert compare(tools, oracle_outer_join, right_left_join) == "match"
