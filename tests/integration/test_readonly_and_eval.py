"""The read-only runner's three limits, and the eval end to end.

Needs `make load`, `make noise` and `make copy-target` first.
"""

import time

import pytest

from warehouse_scout.config import get_settings
from warehouse_scout.evals.translation import build_cases, run_case
from warehouse_scout.translate import SqlglotTranslator
from warehouse_scout.warehouse import Database, run_readonly

pytestmark = pytest.mark.integration

BOTH: list[Database] = ["source", "target"]


@pytest.mark.parametrize("database", BOTH)
def test_select_returns_lower_case_columns_and_rows(database: Database) -> None:
    result = run_readonly(get_settings(), database, "SELECT COUNT(*) AS n FROM item")

    assert result.ok
    assert result.columns == ("n",)
    assert result.rows[0][0] > 0


@pytest.mark.parametrize("database", BOTH)
def test_row_limit(database: Database) -> None:
    result = run_readonly(get_settings(), database, "SELECT * FROM item", max_rows=5)

    assert len(result.rows) == 5
    assert result.truncated


@pytest.mark.parametrize("database", BOTH)
@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM item",
        "DROP TABLE item",
        "UPDATE item SET i_brand = 'x'",
        "CREATE TABLE z (a INT)",
    ],
)
def test_writes_are_refused_by_the_database(database: Database, statement: str) -> None:
    result = run_readonly(get_settings(), database, statement)

    assert not result.ok
    assert result.error_code is not None


@pytest.mark.parametrize(
    ("database", "slow_sql", "error_code"),
    [
        (
            "source",
            "SELECT COUNT(*) FROM customer_demographics a, customer_demographics b",
            "DPY-4024",
        ),
        ("target", "SELECT pg_sleep(10)", "57014"),
    ],
)
def test_timeout_cancels_a_slow_query(database: Database, slow_sql: str, error_code: str) -> None:
    started = time.perf_counter()
    result = run_readonly(get_settings(), database, slow_sql, timeout_seconds=1)

    assert result.error_code == error_code
    assert time.perf_counter() - started < 5


@pytest.mark.parametrize(("database", "error_code"), [("source", "ORA-00904"), ("target", "42703")])
def test_sql_errors_are_returned_not_raised(database: Database, error_code: str) -> None:
    result = run_readonly(get_settings(), database, "SELECT no_such_column FROM item")

    assert result.error_code == error_code
    assert "\n" not in (result.error or "")


def test_trailing_semicolon_and_percent_sign_are_accepted() -> None:
    settings = get_settings()

    assert run_readonly(settings, "source", "SELECT '50%' AS pct FROM dual;").rows == [("50%",)]
    assert run_readonly(settings, "target", "SELECT '50%' AS pct;").rows == [("50%",)]


def test_eval_scores_known_cases_as_expected() -> None:
    settings = get_settings()
    cases = {case.id: case for case in build_cases()}
    translator = SqlglotTranslator()

    passing = run_case(settings, translator, cases["revenue_by_category"])
    broken = run_case(settings, translator, cases["org_chart"])
    wrong = run_case(settings, translator, cases["return_rate_by_item"])

    assert passing.status == "match"
    assert passing.source_rows and passing.source_rows > 0
    assert broken.status == "target_error"  # CONNECT BY is not PostgreSQL
    assert wrong.status == "mismatch"  # (+) became an inner join: runs, wrong numbers
