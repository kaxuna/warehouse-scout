"""Create the warehouse mess declared in `noise_catalog.py` inside Oracle.

Run it after the benchmark is loaded:
    make load
    make noise

It is repeatable: every object is dropped or replaced before it is created.
`--write-manifest` also refreshes the committed answer key.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import oracledb

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_oracle
from warehouse_scout.ingest.noise_catalog import (
    DATA_TRAPS,
    NOISE_FUNCTIONS,
    NOISE_TABLES,
    NOISE_VIEWS,
    NoiseFunction,
    build_manifest,
)
from warehouse_scout.ingest.tpcds_docs import TPCDS_TABLE_COMMENTS

MANIFEST_PATH = Path("evals/golden/planted_defects.json")


def function_ddl(function: NoiseFunction) -> str:
    return (
        f"CREATE OR REPLACE FUNCTION {function.name} {function.signature} DETERMINISTIC IS "
        f"BEGIN RETURN {function.body}; END;"
    )


def comment_ddl(table: str, comment: str) -> str:
    # A comment is a string literal inside DDL, where bind parameters are not
    # allowed. Doubling single quotes is SQL's own escape for them.
    escaped = comment.replace("'", "''")
    return f"COMMENT ON TABLE {table} IS '{escaped}'"


def plant_data_traps(cursor: oracledb.Cursor) -> None:
    for _, statement in DATA_TRAPS:
        cursor.execute(statement)


def create_functions(cursor: oracledb.Cursor, readonly_user: str) -> None:
    for function in NOISE_FUNCTIONS:
        cursor.execute(function_ddl(function))
        # The schema-wide grant covers tables and views; functions need their own.
        cursor.execute(f"GRANT EXECUTE ON {function.name} TO {readonly_user}")


def create_tables(cursor: oracledb.Cursor) -> None:
    for table in NOISE_TABLES:
        cursor.execute(f"DROP TABLE IF EXISTS {table.name} PURGE")
        cursor.execute(f"CREATE TABLE {table.name} AS {table.select_sql}")
        cursor.execute(comment_ddl(table.name, table.comment))


def create_views(cursor: oracledb.Cursor) -> None:
    for view in NOISE_VIEWS:
        cursor.execute(f"CREATE OR REPLACE VIEW {view.name} AS {view.select_sql}")
        cursor.execute(comment_ddl(view.name, view.comment))


def comment_benchmark_tables(cursor: oracledb.Cursor) -> None:
    for table, comment in TPCDS_TABLE_COMMENTS.items():
        cursor.execute(comment_ddl(table, comment))


def invalid_objects(cursor: oracledb.Cursor) -> list[str]:
    """Functions and views Oracle accepted but could not compile."""
    cursor.execute(
        "SELECT object_type || ' ' || object_name FROM user_objects "
        "WHERE status <> 'VALID' ORDER BY 1"
    )
    return [description for (description,) in cursor.fetchall()]


def write_manifest(path: Path = MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_manifest(), indent=2) + "\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create junk tables, views and functions.")
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help=f"also rewrite {MANIFEST_PATH} from the catalog",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()

    with connect_oracle(settings) as ora, ora.cursor() as cursor:
        # Order matters: traps first so table copies include them; a view uses
        # a function, and views read the tables.
        plant_data_traps(cursor)
        ora.commit()
        create_functions(cursor, settings.oracle_ro_user)
        create_tables(cursor)
        create_views(cursor)
        comment_benchmark_tables(cursor)
        broken = invalid_objects(cursor)

    print(
        f"Created {len(NOISE_TABLES)} tables, {len(NOISE_VIEWS)} views, "
        f"{len(NOISE_FUNCTIONS)} functions; commented {len(TPCDS_TABLE_COMMENTS)} "
        "benchmark tables."
    )
    if broken:
        print("Objects that did not compile: " + ", ".join(broken), file=sys.stderr)
        return 1

    if args.write_manifest:
        write_manifest()
        print(f"Wrote {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
