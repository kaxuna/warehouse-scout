"""Checks the junk objects exist in Oracle. Needs `make load` and `make noise` first."""

from collections.abc import Iterator

import oracledb
import pytest

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_oracle
from warehouse_scout.ingest.noise_catalog import NOISE_FUNCTIONS, NOISE_TABLES, NOISE_VIEWS
from warehouse_scout.ingest.noise_tables import invalid_objects
from warehouse_scout.ingest.tpcds_docs import TPCDS_TABLE_COMMENTS

pytestmark = pytest.mark.integration


@pytest.fixture
def owner_cursor() -> Iterator[oracledb.Cursor]:
    with connect_oracle(get_settings()) as connection, connection.cursor() as cursor:
        yield cursor


@pytest.fixture
def readonly_cursor() -> Iterator[oracledb.Cursor]:
    settings = get_settings()
    with connect_oracle(settings, readonly=True) as connection, connection.cursor() as cursor:
        # Resolve unqualified names in the warehouse schema, as the agent's session will.
        cursor.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {settings.oracle_user}")
        yield cursor


def names_in_catalog(cursor: oracledb.Cursor, object_type: str) -> set[str]:
    cursor.execute(
        "SELECT object_name FROM user_objects WHERE object_type = :object_type",
        object_type=object_type,
    )
    return {name for (name,) in cursor.fetchall()}


def test_every_declared_object_exists(owner_cursor: oracledb.Cursor) -> None:
    tables = names_in_catalog(owner_cursor, "TABLE")
    views = names_in_catalog(owner_cursor, "VIEW")
    functions = names_in_catalog(owner_cursor, "FUNCTION")

    assert {t.name.upper() for t in NOISE_TABLES} <= tables
    assert {v.name.upper() for v in NOISE_VIEWS} <= views
    assert {f.name.upper() for f in NOISE_FUNCTIONS} <= functions


def test_everything_compiled(owner_cursor: oracledb.Cursor) -> None:
    assert invalid_objects(owner_cursor) == []


def test_every_table_has_a_comment(owner_cursor: oracledb.Cursor) -> None:
    owner_cursor.execute("SELECT table_name FROM user_tab_comments WHERE comments IS NULL")
    uncommented = {name.lower() for (name,) in owner_cursor.fetchall()}

    expected = {t.name for t in NOISE_TABLES} | set(TPCDS_TABLE_COMMENTS)
    assert uncommented & expected == set()


def test_duplicate_functions_agree(readonly_cursor: oracledb.Cursor) -> None:
    readonly_cursor.execute(
        "SELECT fn_net_price(100, 15), calc_net_amount(100, 15), get_discounted_price(100, 15), "
        "fn_net_price(100, NULL), calc_net_amount(100, NULL), get_discounted_price(100, NULL), "
        "fiscal_quarter(DATE '2024-08-20'), get_qtr(DATE '2024-08-20'), "
        "margin_pct(80, 20), profit_margin(80, 20), "
        "full_name(' Ada ', 'Lovelace'), fmt_customer_name(' Ada ', 'Lovelace') FROM dual"
    )
    assert readonly_cursor.fetchone() == (
        85, 85, 85,
        100, 100, 100,
        3, 3,
        75, 75,
        "Ada Lovelace", "Ada Lovelace",
    )  # fmt: skip


def test_read_only_account_can_query_views_and_hierarchy(
    readonly_cursor: oracledb.Cursor,
) -> None:
    readonly_cursor.execute("SELECT COUNT(*) FROM v_customer_360")
    (customers,) = readonly_cursor.fetchone()
    assert customers > 0

    readonly_cursor.execute(
        "SELECT MAX(LEVEL) FROM employee START WITH manager_id IS NULL "
        "CONNECT BY PRIOR employee_id = manager_id"
    )
    (depth,) = readonly_cursor.fetchone()
    assert depth >= 3
