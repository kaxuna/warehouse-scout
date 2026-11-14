"""Copies a probe table from Oracle to PostgreSQL and compares the two. Needs `make up`."""

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import oracledb
import psycopg
import pytest

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_oracle, connect_postgres
from warehouse_scout.migrate.copy_tables import copy_table, count_rows, describe_table

pytestmark = pytest.mark.integration

TABLE = "IT_COPY_PROBE"


@pytest.fixture
def ora() -> Iterator[oracledb.Connection]:
    with connect_oracle(get_settings()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {TABLE} PURGE")
            cursor.execute(
                f"CREATE TABLE {TABLE} (id NUMBER(19) NOT NULL, label VARCHAR2(40), "
                "price NUMBER(7,2), sold DATE, any_number NUMBER)"
            )
            cursor.execute(
                f"INSERT INTO {TABLE} VALUES (1, 'first', 0.10, "
                "TO_DATE('2024-03-05 13:45:10', 'YYYY-MM-DD HH24:MI:SS'), 1.5)"
            )
            cursor.execute(f"INSERT INTO {TABLE} VALUES (2, NULL, NULL, NULL, NULL)")
            cursor.execute(f"COMMENT ON TABLE {TABLE} IS 'Probe table, it''s temporary'")
        connection.commit()
        yield connection
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {TABLE} PURGE")


@pytest.fixture
def pg() -> Iterator[psycopg.Connection[Any]]:
    with connect_postgres(get_settings()) as connection:
        yield connection
        connection.execute(f"DROP TABLE IF EXISTS target.{TABLE.lower()}")


def test_copy_preserves_rows_types_and_nulls(
    ora: oracledb.Connection, pg: psycopg.Connection[Any]
) -> None:
    assert copy_table(ora, pg, TABLE) == 2
    assert count_rows(pg, TABLE) == 2

    rows = pg.execute(f"SELECT * FROM target.{TABLE.lower()} ORDER BY id").fetchall()
    assert rows == [
        # 0.10 stays exact, and the time of day survives the DATE -> timestamp mapping.
        (1, "first", Decimal("0.10"), datetime(2024, 3, 5, 13, 45, 10), Decimal("1.5")),
        (2, None, None, None, None),
    ]


def test_copy_carries_the_table_comment(
    ora: oracledb.Connection, pg: psycopg.Connection[Any]
) -> None:
    copy_table(ora, pg, TABLE)

    row = pg.execute("SELECT obj_description(%s::regclass)", [f"target.{TABLE.lower()}"]).fetchone()
    assert row == ("Probe table, it's temporary",)


def test_copy_is_repeatable(ora: oracledb.Connection, pg: psycopg.Connection[Any]) -> None:
    copy_table(ora, pg, TABLE)
    copy_table(ora, pg, TABLE)

    assert count_rows(pg, TABLE) == 2


def test_oracle_catalog_is_read_correctly(ora: oracledb.Connection) -> None:
    by_name = {c.name: c for c in describe_table(ora, TABLE)}

    assert (by_name["ID"].precision, by_name["ID"].scale, by_name["ID"].nullable) == (19, 0, False)
    assert by_name["LABEL"].char_length == 40
    assert by_name["ANY_NUMBER"].precision is None


def test_read_only_role_can_read_the_copy_but_not_change_it(
    ora: oracledb.Connection, pg: psycopg.Connection[Any]
) -> None:
    copy_table(ora, pg, TABLE)

    with connect_postgres(get_settings(), readonly=True) as readonly:
        assert readonly.execute(f"SELECT COUNT(*) FROM {TABLE.lower()}").fetchone() == (2,)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            readonly.execute(f"DELETE FROM target.{TABLE.lower()}")
