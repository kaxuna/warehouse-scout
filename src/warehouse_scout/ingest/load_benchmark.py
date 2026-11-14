"""Load the TPC-DS benchmark into Oracle.

Pipeline:
    DuckDB (in memory) generates TPC-DS  ->  read schema  ->  CREATE TABLE in Oracle
                                         ->  read rows in batches  ->  INSERT into Oracle

Run it:
    make load                                  # default scale factor
    make load ARGS="--scale-factor 0.1"        # roughly ten times the rows

The scale factor is TPC-DS's size knob: 1 means about 1 GB of raw data.
"""

import argparse
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import duckdb
import oracledb

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_oracle
from warehouse_scout.sqlutil import checked_identifier

DEFAULT_SCALE_FACTOR = 0.01
DEFAULT_BATCH_SIZE = 10_000

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
    return f"CREATE TABLE {checked_identifier(table)} ({column_defs})"


def _column_ddl(column: Column) -> str:
    not_null = "" if column.nullable else " NOT NULL"
    return f"{checked_identifier(column.name)} {oracle_type(column.duckdb_type)}{not_null}"


# --- Reading from DuckDB -------------------------------------------------------


def generate_tpcds(duck: duckdb.DuckDBPyConnection, scale_factor: float) -> None:
    """Generate the TPC-DS data set inside `duck` using DuckDB's tpcds extension."""
    duck.execute("INSTALL tpcds")
    duck.execute("LOAD tpcds")
    duck.execute("CALL dsdgen(sf = ?)", [scale_factor])


def list_tables(duck: duckdb.DuckDBPyConnection) -> list[str]:
    """Names of all tables in DuckDB's default schema, sorted."""
    rows = duck.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()
    return [name for (name,) in rows]


def describe_table(duck: duckdb.DuckDBPyConnection, table: str) -> list[Column]:
    """Columns of `table`, in their declared order."""
    rows = duck.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return [
        Column(name=name, duckdb_type=data_type, nullable=(is_nullable == "YES"))
        for name, data_type, is_nullable in rows
    ]


def iter_batches(
    duck: duckdb.DuckDBPyConnection, table: str, batch_size: int
) -> Iterator[list[tuple[Any, ...]]]:
    """Yield the rows of `table` in lists of at most `batch_size`.

    A generator: rows are pulled from DuckDB only as the caller asks for the
    next batch, so memory use stays at one batch however large the table is.
    """
    # A separate cursor, so other queries on `duck` cannot disturb this result set.
    cursor = duck.cursor()
    cursor.execute(f"SELECT * FROM {checked_identifier(table)}")
    while batch := cursor.fetchmany(batch_size):
        yield batch


# --- Writing to Oracle ---------------------------------------------------------


def load_table(
    duck: duckdb.DuckDBPyConnection,
    ora: oracledb.Connection,
    table: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Drop and recreate `table` in Oracle, copy all rows, return the row count."""
    columns = describe_table(duck, table)
    ddl = create_table_ddl(table, columns)  # also validates every identifier

    # Values travel as bind parameters (:1, :2, ...), never as SQL text.
    placeholders = ", ".join(f":{position}" for position in range(1, len(columns) + 1))
    insert_sql = f"INSERT INTO {table} VALUES ({placeholders})"

    row_count = 0
    with ora.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {table} PURGE")
        cursor.execute(ddl)
        for batch in iter_batches(duck, table, batch_size):
            cursor.executemany(insert_sql, batch)
            row_count += len(batch)
    ora.commit()
    return row_count


def count_rows(ora: oracledb.Connection, table: str) -> int:
    with ora.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {checked_identifier(table)}")
        (count,) = cursor.fetchone()
    return int(count)


# --- Command line --------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load the TPC-DS benchmark into Oracle.")
    parser.add_argument("--scale-factor", type=float, default=DEFAULT_SCALE_FACTOR)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Load every table and verify the row counts. Returns a process exit code."""
    args = parse_args(argv)
    settings = get_settings()

    with duckdb.connect() as duck, connect_oracle(settings) as ora:
        print(f"Generating TPC-DS at scale factor {args.scale_factor} ...")
        generate_tpcds(duck, args.scale_factor)

        mismatches = 0
        for table in list_tables(duck):
            loaded = load_table(duck, ora, table, args.batch_size)
            in_oracle = count_rows(ora, table)
            status = "ok" if in_oracle == loaded else "MISMATCH"
            mismatches += status != "ok"
            print(f"  {table:<24} {loaded:>10,} rows  {status}")

    if mismatches:
        print(f"{mismatches} table(s) have a different row count in Oracle.", file=sys.stderr)
        return 1
    print("All tables loaded and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
