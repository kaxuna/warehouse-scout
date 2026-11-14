"""End-to-end loader tests against the Oracle container. Run with `make test-integration`."""

from collections.abc import Iterator

import duckdb
import oracledb
import pytest

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_oracle
from warehouse_scout.ingest.load_benchmark import count_rows, load_table

pytestmark = pytest.mark.integration

TABLE = "it_loader_probe"


@pytest.fixture
def duck() -> Iterator[duckdb.DuckDBPyConnection]:
    with duckdb.connect() as connection:
        connection.execute(
            f"CREATE TABLE {TABLE} (id BIGINT NOT NULL, label VARCHAR, "
            "price DECIMAL(7,2), sold DATE)"
        )
        connection.execute(
            f"INSERT INTO {TABLE} "
            "SELECT i, 'row ' || i, i * 1.25, DATE '2024-01-01' + CAST(i AS INTEGER) "
            "FROM range(25) t(i)"
        )
        connection.execute(f"INSERT INTO {TABLE} VALUES (99, NULL, NULL, NULL)")
        yield connection


@pytest.fixture
def ora() -> Iterator[oracledb.Connection]:
    with connect_oracle(get_settings()) as connection:
        yield connection
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {TABLE} PURGE")


def test_load_table_copies_every_row(
    duck: duckdb.DuckDBPyConnection, ora: oracledb.Connection
) -> None:
    loaded = load_table(duck, ora, TABLE, batch_size=10)

    assert loaded == 26
    assert count_rows(ora, TABLE) == 26


def test_load_table_preserves_values_and_nulls(
    duck: duckdb.DuckDBPyConnection, ora: oracledb.Connection
) -> None:
    load_table(duck, ora, TABLE, batch_size=10)

    with ora.cursor() as cursor:
        cursor.execute(
            f"SELECT label, price, TO_CHAR(sold, 'YYYY-MM-DD') FROM {TABLE} WHERE id = 4"
        )
        assert cursor.fetchone() == ("row 4", 5, "2024-01-05")
        cursor.execute(f"SELECT label, price, sold FROM {TABLE} WHERE id = 99")
        assert cursor.fetchone() == (None, None, None)


def test_load_table_is_repeatable(
    duck: duckdb.DuckDBPyConnection, ora: oracledb.Connection
) -> None:
    load_table(duck, ora, TABLE, batch_size=10)
    load_table(duck, ora, TABLE, batch_size=10)

    assert count_rows(ora, TABLE) == 26


def test_read_only_account_can_read_but_not_write(
    duck: duckdb.DuckDBPyConnection, ora: oracledb.Connection
) -> None:
    load_table(duck, ora, TABLE, batch_size=10)
    settings = get_settings()

    with connect_oracle(settings, readonly=True) as readonly, readonly.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {settings.oracle_user}.{TABLE}")
        assert cursor.fetchone() == (26,)
        with pytest.raises(oracledb.DatabaseError):
            cursor.execute(f"DELETE FROM {settings.oracle_user}.{TABLE}")
