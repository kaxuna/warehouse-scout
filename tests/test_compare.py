"""Tests for result comparison. Results are built by hand: no database needed."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from warehouse_scout.compare import compare_results, normalize_rows, normalize_value
from warehouse_scout.warehouse import QueryResult


def result(*rows: tuple[Any, ...], columns: int | None = None, **extra: Any) -> QueryResult:
    width = columns if columns is not None else (len(rows[0]) if rows else 1)
    return QueryResult(columns=tuple(f"c{i}" for i in range(width)), rows=list(rows), **extra)


@pytest.mark.parametrize(
    ("oracle_value", "postgres_value"),
    [
        (5, Decimal("5.000")),
        (Decimal("0.1"), 0.1),
        (Decimal("10.17685393258426966292134831460674157303"), Decimal("10.1768539325842697")),
        (datetime(2024, 3, 5), date(2024, 3, 5)),
        (datetime(2024, 3, 5, 13, 45), datetime(2024, 3, 5, 13, 45, tzinfo=UTC)),
        ("text", "text"),
        (None, None),
    ],
)
def test_same_data_from_two_engines_normalizes_equal(
    oracle_value: Any, postgres_value: Any
) -> None:
    assert normalize_value(oracle_value) == normalize_value(postgres_value)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (5, 6),
        (Decimal("1.00001"), Decimal("1.00002")),
        (datetime(2024, 3, 5, 13, 45), date(2024, 3, 5)),  # a time of day is real data
        ("a", "A"),
        (None, 0),
        (None, ""),  # Oracle has no empty string; PostgreSQL returning '' is a difference
    ],
)
def test_different_data_stays_different(left: Any, right: Any) -> None:
    assert normalize_value(left) != normalize_value(right)


def test_normalize_rows_sorts_rows_including_nulls_and_mixed_types() -> None:
    rows = [("b", None), (None, 2), ("a", 1)]
    assert normalize_rows(rows) == normalize_rows(list(reversed(rows)))


def test_match_ignores_row_order_and_number_representation() -> None:
    source = result(("Shoes", 178), ("Books", 12))
    target = result(("Books", Decimal("12.0")), ("Shoes", Decimal("178")))

    comparison = compare_results(source, target)

    assert comparison.status == "match"
    assert (comparison.source_rows, comparison.target_rows) == (2, 2)


def test_column_names_are_not_compared() -> None:
    source = QueryResult(columns=("REVENUE",), rows=[(1,)])
    target = QueryResult(columns=("revenue_total",), rows=[(1,)])

    assert compare_results(source, target).status == "match"


def test_mismatch_reports_row_counts() -> None:
    comparison = compare_results(result((1,), (2,)), result((1,)))

    assert comparison.status == "mismatch"
    assert comparison.detail == "2 rows on source, 1 on target"


def test_mismatch_reports_column_counts() -> None:
    comparison = compare_results(result((1, 2)), result((1,)))

    assert comparison.status == "mismatch"
    assert comparison.detail == "2 columns on source, 1 on target"


def test_mismatch_counts_differing_rows() -> None:
    comparison = compare_results(result((1, "a"), (2, "b")), result((1, "a"), (2, "X")))

    assert comparison.status == "mismatch"
    assert comparison.detail == "1 of 2 rows differ"


def test_duplicate_rows_count() -> None:
    assert compare_results(result((1,), (1,), (2,)), result((1,), (2,), (2,))).status == "mismatch"


def test_empty_results_match() -> None:
    assert compare_results(result(columns=2), result(columns=2)).status == "match"


def test_errors_are_reported_by_side() -> None:
    failed = QueryResult(error="boom", error_code="42601")

    assert compare_results(failed, result((1,))).status == "source_error"
    target_failure = compare_results(result((1,)), failed)
    assert target_failure.status == "target_error"
    assert target_failure.detail == "42601: boom"


def test_truncated_results_are_not_comparable() -> None:
    comparison = compare_results(result((1,), truncated=True), result((1,)))

    assert comparison.status == "not_comparable"
