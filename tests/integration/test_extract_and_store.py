"""Extraction from Oracle and storage in PostgreSQL. Needs `make load` and `make noise`."""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_oracle, connect_postgres
from warehouse_scout.extract.oracle import SchemaObject, extract_schema
from warehouse_scout.ingest.noise_catalog import NOISE_FUNCTIONS, NOISE_TABLES, NOISE_VIEWS
from warehouse_scout.ingest.query_log import generate_log
from warehouse_scout.store.snapshot import ensure_schema, store_query_log, store_schema

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def objects() -> dict[str, SchemaObject]:
    settings = get_settings()
    with connect_oracle(settings, readonly=True) as ora:
        return {o.name: o for o in extract_schema(ora, settings.oracle_user)}


@pytest.fixture
def pg() -> Iterator[psycopg.Connection[Any]]:
    """A connection whose changes are thrown away after the test."""
    with connect_postgres(get_settings()) as connection:
        ensure_schema(connection)
        yield connection
        connection.rollback()


def test_extract_finds_every_table_view_and_function(objects: dict[str, SchemaObject]) -> None:
    by_type = {
        kind: {o.name for o in objects.values() if o.object_type == kind}
        for kind in ("TABLE", "VIEW", "FUNCTION")
    }

    assert len(by_type["TABLE"]) == 24 + len(NOISE_TABLES)
    assert by_type["VIEW"] == {v.name.upper() for v in NOISE_VIEWS}
    assert by_type["FUNCTION"] == {f.name.upper() for f in NOISE_FUNCTIONS}


def test_table_carries_comment_row_count_and_typed_columns(
    objects: dict[str, SchemaObject],
) -> None:
    item = objects["ITEM"]

    assert item.comment == "Dimension: products with brand, class, category and prices."
    assert item.row_count is not None and item.row_count > 0
    assert item.columns[0].name == "I_ITEM_SK"
    assert {c.data_type for c in item.columns} >= {"NUMBER(19)", "VARCHAR2(4000)", "NUMBER(7,2)"}


def test_view_and_function_carry_their_source(objects: dict[str, SchemaObject]) -> None:
    view = objects["V_CUSTOMER_360"]
    function = objects["GET_QTR"]

    assert "(+)" in (view.definition or "")
    assert [c.name for c in view.columns][:2] == ["C_CUSTOMER_SK", "C_FIRST_NAME"]
    assert "EXTRACT(MONTH FROM d)" in (function.definition or "")
    assert function.columns == ()


def test_schema_round_trip(objects: dict[str, SchemaObject], pg: psycopg.Connection[Any]) -> None:
    store_schema(pg, list(objects.values()))
    store_schema(pg, list(objects.values()))  # storing twice replaces, never duplicates

    counts = dict(
        pg.execute("SELECT object_type, COUNT(*) FROM scout.schema_object GROUP BY 1").fetchall()
    )
    assert counts == {"TABLE": 24 + len(NOISE_TABLES), "VIEW": 4, "FUNCTION": 12}

    columns = pg.execute(
        "SELECT column_name, data_type FROM scout.schema_column "
        "WHERE object_name = 'INCOME_BAND' ORDER BY position"
    ).fetchall()
    assert columns[0] == ("IB_INCOME_BAND_SK", "NUMBER(19)")


def test_query_log_round_trip(pg: psycopg.Connection[Any]) -> None:
    entries = generate_log(days=1)

    assert store_query_log(pg, entries) == len(entries)
    assert store_query_log(pg, entries) == len(entries)  # replaces, never appends

    row = pg.execute("SELECT COUNT(*), MIN(started_at) FROM scout.query_log").fetchone()
    assert row is not None
    assert row[0] == len(entries)
    assert row[1].isoformat() == entries[0].started_at


def test_read_only_role_cannot_see_scouts_own_tables() -> None:
    # The agent's database account reads the migrated data, not Scout's metadata.
    with (
        connect_postgres(get_settings(), readonly=True) as readonly,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        readonly.execute("SELECT COUNT(*) FROM scout.schema_object")
