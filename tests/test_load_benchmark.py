"""Tests for the schema-translation functions of the loader. No database needed."""

import pytest

from warehouse_scout.ingest.load_benchmark import Column, create_table_ddl, oracle_type


@pytest.mark.parametrize(
    ("duckdb_type", "expected"),
    [
        ("INTEGER", "NUMBER(10)"),
        ("BIGINT", "NUMBER(19)"),
        ("DECIMAL(7,2)", "NUMBER(7,2)"),
        ("DECIMAL(15,2)", "NUMBER(15,2)"),
        ("VARCHAR", "VARCHAR2(4000)"),
        ("DATE", "DATE"),
    ],
)
def test_oracle_type_maps_every_tpcds_type(duckdb_type: str, expected: str) -> None:
    assert oracle_type(duckdb_type) == expected


def test_oracle_type_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="HUGEINT"):
        oracle_type("HUGEINT")


def test_create_table_ddl() -> None:
    columns = [
        Column(name="i_item_sk", duckdb_type="BIGINT", nullable=False),
        Column(name="i_item_desc", duckdb_type="VARCHAR", nullable=True),
        Column(name="i_current_price", duckdb_type="DECIMAL(7,2)", nullable=True),
    ]

    assert create_table_ddl("item", columns) == (
        "CREATE TABLE item ("
        "i_item_sk NUMBER(19) NOT NULL, "
        "i_item_desc VARCHAR2(4000), "
        "i_current_price NUMBER(7,2))"
    )


def test_create_table_ddl_rejects_table_without_columns() -> None:
    with pytest.raises(ValueError, match="no columns"):
        create_table_ddl("item", [])


@pytest.mark.parametrize("bad_name", ["item; DROP TABLE x", "1item", "it em", ""])
def test_create_table_ddl_rejects_unsafe_identifiers(bad_name: str) -> None:
    safe_column = Column(name="id", duckdb_type="BIGINT", nullable=False)
    bad_column = Column(name=bad_name, duckdb_type="BIGINT", nullable=False)

    with pytest.raises(ValueError, match="identifier"):
        create_table_ddl(bad_name, [safe_column])
    with pytest.raises(ValueError, match="identifier"):
        create_table_ddl("item", [bad_column])
