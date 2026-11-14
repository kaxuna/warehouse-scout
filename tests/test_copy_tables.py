"""Tests for the Oracle-to-PostgreSQL type mapping and DDL. No database needed."""

import pytest

from warehouse_scout.migrate.copy_tables import OracleColumn, create_table_ddl, postgres_type
from warehouse_scout.sqlutil import checked_identifier


def column(
    data_type: str,
    *,
    precision: int | None = None,
    scale: int | None = None,
    char_length: int | None = None,
    name: str = "C",
    nullable: bool = True,
) -> OracleColumn:
    """Build a column with only the fields a test cares about spelled out."""
    return OracleColumn(name, data_type, precision, scale, char_length, nullable)


@pytest.mark.parametrize(
    ("oracle_column", "expected"),
    [
        (column("NUMBER", precision=9, scale=0), "integer"),
        (column("NUMBER", precision=10, scale=0), "bigint"),  # 10 digits overflow int32
        (column("NUMBER", precision=18, scale=0), "bigint"),
        (column("NUMBER", precision=19, scale=0), "numeric(19)"),  # 19 digits overflow int64
        (column("NUMBER", precision=7, scale=2), "numeric(7,2)"),
        (column("NUMBER"), "numeric"),
        (column("VARCHAR2", char_length=4000), "varchar(4000)"),
        (column("CHAR", char_length=1), "char(1)"),
        (column("DATE"), "timestamp(0)"),
    ],
)
def test_postgres_type(oracle_column: OracleColumn, expected: str) -> None:
    assert postgres_type(oracle_column) == expected


def test_postgres_type_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="BLOB"):
        postgres_type(column("BLOB"))


def test_create_table_ddl_lower_cases_names_and_targets_the_schema() -> None:
    columns = [
        column("NUMBER", precision=19, scale=0, name="I_ITEM_SK", nullable=False),
        column("VARCHAR2", char_length=4000, name="I_ITEM_DESC"),
    ]

    assert create_table_ddl("ITEM", columns) == (
        "CREATE TABLE target.item (i_item_sk numeric(19) NOT NULL, i_item_desc varchar(4000))"
    )


def test_create_table_ddl_rejects_unsafe_names_and_empty_tables() -> None:
    with pytest.raises(ValueError, match="identifier"):
        create_table_ddl("ITEM; DROP TABLE x", [column("DATE")])
    with pytest.raises(ValueError, match="no columns"):
        create_table_ddl("ITEM", [])


@pytest.mark.parametrize("name", ["item", "I_ITEM_SK", "t1"])
def test_checked_identifier_accepts_plain_names(name: str) -> None:
    assert checked_identifier(name) == name


@pytest.mark.parametrize("name", ["", "1t", "a b", "a;b", 'a"b', "a.b"])
def test_checked_identifier_rejects_everything_else(name: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        checked_identifier(name)
