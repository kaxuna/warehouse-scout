"""Tests for the DuckDB-reading half of the loader.

They build a tiny table in an in-memory DuckDB instead of generating TPC-DS,
so they need neither the tpcds extension download nor a running database.
"""

from collections.abc import Iterator

import duckdb
import pytest

from warehouse_scout.ingest.load_benchmark import (
    Column,
    describe_table,
    iter_batches,
    list_tables,
)


@pytest.fixture
def duck() -> Iterator[duckdb.DuckDBPyConnection]:
    """An in-memory DuckDB holding two small tables. Closed after the test."""
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE zebra (id BIGINT NOT NULL, label VARCHAR)")
        connection.execute("CREATE TABLE apple (price DECIMAL(7,2), sold DATE)")
        connection.execute("INSERT INTO zebra SELECT i, 'row ' || i FROM range(5) t(i)")
        yield connection


def test_list_tables_is_sorted(duck: duckdb.DuckDBPyConnection) -> None:
    assert list_tables(duck) == ["apple", "zebra"]


def test_describe_table_keeps_declared_order_and_nullability(
    duck: duckdb.DuckDBPyConnection,
) -> None:
    assert describe_table(duck, "zebra") == [
        Column(name="id", duckdb_type="BIGINT", nullable=False),
        Column(name="label", duckdb_type="VARCHAR", nullable=True),
    ]


def test_describe_table_reports_parameterised_types(duck: duckdb.DuckDBPyConnection) -> None:
    assert [column.duckdb_type for column in describe_table(duck, "apple")] == [
        "DECIMAL(7,2)",
        "DATE",
    ]


def test_iter_batches_splits_rows_and_keeps_all_of_them(
    duck: duckdb.DuckDBPyConnection,
) -> None:
    batches = list(iter_batches(duck, "zebra", batch_size=2))

    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert sorted(row for batch in batches for row in batch) == [
        (0, "row 0"),
        (1, "row 1"),
        (2, "row 2"),
        (3, "row 3"),
        (4, "row 4"),
    ]


def test_iter_batches_on_empty_table_yields_nothing(duck: duckdb.DuckDBPyConnection) -> None:
    assert list(iter_batches(duck, "apple", batch_size=10)) == []


def test_iter_batches_rejects_unsafe_table_name(duck: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="identifier"):
        list(iter_batches(duck, "zebra; DROP TABLE zebra", batch_size=10))
