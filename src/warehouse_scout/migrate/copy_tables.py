"""Copy every table from the Oracle source schema into PostgreSQL's `target` schema.

    make copy-target

This is the data half of a migration: table definitions and rows. It gives
translated queries something real to run against, so "does the translation
work?" is answered by PostgreSQL instead of by opinion.

Views and functions are code, not data, and are deliberately not copied: a
translated query that still needs `fiscal_quarter()` fails on the target
until someone migrates that function. Surfacing that is part of the tool's job.
"""

import argparse
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import oracledb
import psycopg
from psycopg import sql

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_oracle, connect_postgres
from warehouse_scout.sqlutil import checked_identifier

TARGET_SCHEMA = "target"
DEFAULT_BATCH_SIZE = 10_000


@dataclass(frozen=True)
class OracleColumn:
    """One column as Oracle's catalog (USER_TAB_COLUMNS) describes it."""

    name: str
    data_type: str  # "NUMBER", "VARCHAR2", "DATE", ...
    precision: int | None
    scale: int | None
    char_length: int | None
    nullable: bool


def postgres_type(column: OracleColumn) -> str:
    """Map an Oracle column type to the PostgreSQL type that holds the same values."""
    match column.data_type:
        case "NUMBER":
            return _postgres_number(column.precision, column.scale)
        case "VARCHAR2" | "NVARCHAR2":
            return f"varchar({column.char_length})"
        case "CHAR" | "NCHAR":
            return f"char({column.char_length})"
        case "DATE":
            # Oracle's DATE carries a time of day down to the second.
            # PostgreSQL's date would silently drop it; timestamp(0) keeps it.
            return "timestamp(0)"
        case _:
            raise ValueError(f"No PostgreSQL mapping for Oracle type {column.data_type!r}")


def _postgres_number(precision: int | None, scale: int | None) -> str:
    if precision is None:
        return "numeric"  # NUMBER with no declared size: any value fits
    if scale:
        return f"numeric({precision},{scale})"
    # Whole numbers: pick the smallest integer type that holds `precision` digits.
    if precision <= 9:
        return "integer"  # up to 2,147,483,647
    if precision <= 18:
        return "bigint"  # up to about 9.2e18
    return f"numeric({precision})"


def create_table_ddl(table: str, columns: Sequence[OracleColumn]) -> str:
    """Build the PostgreSQL CREATE TABLE statement. Names are lower-cased:
    Oracle stores unquoted names in upper case, PostgreSQL in lower case."""
    if not columns:
        raise ValueError(f"Table {table!r} has no columns")
    column_defs = ", ".join(
        f"{checked_identifier(column.name).lower()} {postgres_type(column)}"
        + ("" if column.nullable else " NOT NULL")
        for column in columns
    )
    return f"CREATE TABLE {TARGET_SCHEMA}.{checked_identifier(table).lower()} ({column_defs})"


def list_tables(ora: oracledb.Connection) -> list[str]:
    with ora.cursor() as cursor:
        cursor.execute("SELECT table_name FROM user_tables ORDER BY table_name")
        return [name for (name,) in cursor.fetchall()]


def describe_table(ora: oracledb.Connection, table: str) -> list[OracleColumn]:
    with ora.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, data_precision, data_scale, char_length, nullable "
            "FROM user_tab_columns WHERE table_name = :table_name ORDER BY column_id",
            table_name=table,
        )
        return [
            OracleColumn(
                name=name,
                data_type=data_type,
                precision=precision,
                scale=scale,
                char_length=char_length,
                nullable=(nullable == "Y"),
            )
            for name, data_type, precision, scale, char_length, nullable in cursor.fetchall()
        ]


def table_comment(ora: oracledb.Connection, table: str) -> str | None:
    with ora.cursor() as cursor:
        cursor.execute(
            "SELECT comments FROM user_tab_comments WHERE table_name = :table_name",
            table_name=table,
        )
        row = cursor.fetchone()
    return row[0] if row else None


def iter_rows(
    ora: oracledb.Connection, table: str, batch_size: int
) -> Iterator[list[tuple[Any, ...]]]:
    with ora.cursor() as cursor:
        cursor.arraysize = batch_size  # rows fetched per network round trip
        cursor.execute(f"SELECT * FROM {checked_identifier(table)}")
        while batch := cursor.fetchmany():
            yield batch


def copy_table(
    ora: oracledb.Connection,
    pg: psycopg.Connection[Any],
    table: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Recreate one Oracle table in PostgreSQL and copy its rows. Returns the row count."""
    columns = describe_table(ora, table)
    ddl = create_table_ddl(table, columns)  # also validates every identifier
    qualified = f"{TARGET_SCHEMA}.{table.lower()}"

    row_count = 0
    with pg.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {qualified} CASCADE")
        cursor.execute(ddl)
        # COPY is PostgreSQL's bulk-load path: one streamed statement instead
        # of one INSERT per row.
        with cursor.copy(f"COPY {qualified} FROM STDIN") as copy:
            for batch in iter_rows(ora, table, batch_size):
                for row in batch:
                    copy.write_row(row)
                row_count += len(batch)

        comment = table_comment(ora, table)
        if comment:
            # psycopg's sql module quotes the identifier and the literal correctly.
            cursor.execute(
                sql.SQL("COMMENT ON TABLE {} IS {}").format(
                    sql.Identifier(TARGET_SCHEMA, table.lower()),
                    sql.Literal(comment),
                )
            )
    pg.commit()
    return row_count


def count_rows(pg: psycopg.Connection[Any], table: str) -> int:
    row = pg.execute(
        f"SELECT COUNT(*) FROM {TARGET_SCHEMA}.{checked_identifier(table).lower()}"
    ).fetchone()
    assert row is not None  # COUNT(*) always returns one row
    return int(row[0])


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy Oracle tables into PostgreSQL.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()

    with connect_oracle(settings) as ora, connect_postgres(settings) as pg:
        tables = list_tables(ora)
        total_rows = 0
        mismatches: list[str] = []
        for table in tables:
            copied = copy_table(ora, pg, table, args.batch_size)
            total_rows += copied
            if count_rows(pg, table) != copied:
                mismatches.append(table)

    print(f"Copied {len(tables)} tables, {total_rows:,} rows, into schema {TARGET_SCHEMA!r}.")
    if mismatches:
        print("Row count differs for: " + ", ".join(mismatches), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
