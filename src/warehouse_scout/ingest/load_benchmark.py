"""Load the TPC-DS benchmark into Oracle.

Pipeline:
    DuckDB (in memory) generates TPC-DS  ->  read schema  ->  CREATE TABLE in Oracle
                                         ->  read rows in batches  ->  INSERT into Oracle

This module currently holds the schema-translation half: mapping DuckDB column
types to Oracle and building the CREATE TABLE statement. Both are pure
functions, so they are tested without a database.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

# DuckDB type -> Oracle type, for types with no parameters.
#
# NUMBER(10) and NUMBER(19) are the smallest Oracle numbers that hold every
# 32-bit and 64-bit integer. VARCHAR2 needs an explicit length; DuckDB's VARCHAR
# is unbounded, so we take 4000, the largest size a standard Oracle setup allows.
_FIXED_TYPES = {
    "INTEGER": "NUMBER(10)",
    "BIGINT": "NUMBER(19)",
    "VARCHAR": "VARCHAR2(4000)",
    "DATE": "DATE",
}

# Table and column names are placed into DDL as text, because SQL bind
# parameters cannot stand in for identifiers. Accepting only plain names keeps
# anything unexpected out of the statement.
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Column:
    """One column of a source table, as DuckDB reports it."""

    name: str
    duckdb_type: str  # e.g. "BIGINT", "VARCHAR", "DECIMAL(7,2)", "DATE"
    nullable: bool


def oracle_type(duckdb_type: str) -> str:
    """Map a DuckDB type name to the Oracle type that can hold it.

    Raises ValueError for an unmapped type: a silent wrong guess here would
    corrupt data downstream.
    """
    if duckdb_type in _FIXED_TYPES:
        return _FIXED_TYPES[duckdb_type]

    # "DECIMAL(7,2)" -> "NUMBER(7,2)": same precision and scale, different name.
    if duckdb_type.startswith("DECIMAL("):
        return "NUMBER" + duckdb_type.removeprefix("DECIMAL")

    raise ValueError(f"No Oracle mapping for DuckDB type {duckdb_type!r}")


def create_table_ddl(table: str, columns: Sequence[Column]) -> str:
    """Build the Oracle CREATE TABLE statement for `table`."""
    if not columns:
        raise ValueError(f"Table {table!r} has no columns")

    column_defs = ", ".join(_column_ddl(column) for column in columns)
    return f"CREATE TABLE {_checked_identifier(table)} ({column_defs})"


def _column_ddl(column: Column) -> str:
    not_null = "" if column.nullable else " NOT NULL"
    return f"{_checked_identifier(column.name)} {oracle_type(column.duckdb_type)}{not_null}"


def _checked_identifier(name: str) -> str:
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"Not a plain SQL identifier: {name!r}")
    return name
