"""Store what was extracted from the source in Scout's own PostgreSQL schema.

    make extract

After this, questions about the schema and the workload are answered from
Scout's copy of the metadata; the source warehouse is only needed again to
run queries on real data.
"""

import argparse
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import psycopg

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_oracle, connect_postgres
from warehouse_scout.extract.oracle import SchemaObject, extract_schema
from warehouse_scout.ingest.query_log import DEFAULT_OUTPUT, LogEntry, read_jsonl

SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def ensure_schema(pg: psycopg.Connection[Any]) -> None:
    """Create Scout's tables if they are missing."""
    pg.execute(SCHEMA_SQL.read_text())


def store_schema(pg: psycopg.Connection[Any], objects: Sequence[SchemaObject]) -> None:
    """Replace the stored schema snapshot with `objects`."""
    with pg.cursor() as cursor:
        # Deleting objects also deletes their columns (ON DELETE CASCADE).
        cursor.execute("DELETE FROM scout.schema_object")
        cursor.executemany(
            "INSERT INTO scout.schema_object "
            "(object_name, object_type, comment, row_count, definition) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(o.name, o.object_type, o.comment, o.row_count, o.definition) for o in objects],
        )
        cursor.executemany(
            "INSERT INTO scout.schema_column "
            "(object_name, position, column_name, data_type, nullable) "
            "VALUES (%s, %s, %s, %s, %s)",
            [
                (o.name, position, column.name, column.data_type, column.nullable)
                for o in objects
                for position, column in enumerate(o.columns, start=1)
            ],
        )


def store_query_log(pg: psycopg.Connection[Any], entries: Iterable[LogEntry]) -> int:
    """Replace the stored query log with `entries`. Returns how many were stored."""
    count = 0
    with pg.cursor() as cursor:
        cursor.execute("TRUNCATE scout.query_log RESTART IDENTITY")
        with cursor.copy(
            "COPY scout.query_log (started_at, user_name, sql, elapsed_ms) FROM STDIN"
        ) as copy:
            for entry in entries:
                copy.write_row((entry.started_at, entry.user, entry.sql, entry.elapsed_ms))
                count += 1
    return count


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract source metadata into Scout's store.")
    parser.add_argument("--query-log", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()

    if not args.query_log.exists():
        print(f"{args.query_log} not found. Run `make querylog` first.", file=sys.stderr)
        return 1

    with connect_oracle(settings, readonly=True) as ora:
        objects = extract_schema(ora, settings.oracle_user)

    # One transaction: the snapshot is replaced completely or not at all.
    with connect_postgres(settings) as pg:
        ensure_schema(pg)
        store_schema(pg, objects)
        entries = store_query_log(pg, read_jsonl(args.query_log))

    by_type = {t: sum(o.object_type == t for o in objects) for t in ("TABLE", "VIEW", "FUNCTION")}
    print(
        f"Stored {by_type['TABLE']} tables, {by_type['VIEW']} views, "
        f"{by_type['FUNCTION']} functions and {entries:,} query log entries."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
