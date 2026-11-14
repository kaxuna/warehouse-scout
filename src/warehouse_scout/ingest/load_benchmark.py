"""Load the TPC-DS benchmark into Oracle.  <-- YOUR EXERCISE (week 1)

Pipeline:
    DuckDB (in memory) generates TPC-DS  ->  read schema  ->  CREATE TABLE in Oracle
                                         ->  read rows in batches  ->  INSERT into Oracle

Run it:
    make load                # default scale factor
    uv run python -m warehouse_scout.ingest.load_benchmark --scale-factor 0.1

The function signatures below are a suggested decomposition, not a contract.
Replace each `raise NotImplementedError` with your code; restructure if you
find a better shape. `tests/test_load_benchmark.py` is the spec for the two
pure functions — make it pass first, before touching a database.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import duckdb
import oracledb


@dataclass(frozen=True)
class Column:
    """One column of a source table, as DuckDB reports it."""

    name: str
    duckdb_type: str  # e.g. "BIGINT", "VARCHAR", "DECIMAL(7,2)", "DATE"
    nullable: bool


def generate_tpcds(duck: duckdb.DuckDBPyConnection, scale_factor: float) -> None:
    """Install the tpcds extension and generate the data set inside `duck`."""
    raise NotImplementedError


def list_tables(duck: duckdb.DuckDBPyConnection) -> list[str]:
    """Names of all generated tables, sorted."""
    raise NotImplementedError


def describe_table(duck: duckdb.DuckDBPyConnection, table: str) -> list[Column]:
    """Columns of `table`, in their declared order."""
    raise NotImplementedError


def oracle_type(duckdb_type: str) -> str:
    """Map a DuckDB type name to the Oracle type that can hold it.

    Raise ValueError for a type you have not mapped — a silent wrong guess here
    corrupts data downstream.
    """
    raise NotImplementedError


def create_table_ddl(table: str, columns: list[Column]) -> str:
    """Build the Oracle CREATE TABLE statement for `table`."""
    raise NotImplementedError


def iter_batches(
    duck: duckdb.DuckDBPyConnection, table: str, batch_size: int
) -> Iterator[list[tuple[Any, ...]]]:
    """Yield the rows of `table` in lists of at most `batch_size`.

    Must not hold the whole table in memory at once.
    """
    raise NotImplementedError


def load_table(
    duck: duckdb.DuckDBPyConnection,
    ora: oracledb.Connection,
    table: str,
    batch_size: int,
) -> int:
    """Drop-and-recreate `table` in Oracle, copy all rows, return the row count."""
    raise NotImplementedError


def main() -> None:
    """Parse CLI arguments, load every table, print a per-table summary."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
