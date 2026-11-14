"""Run one query against the source (Oracle) or the target (PostgreSQL), safely.

Three limits apply to every call, whoever wrote the SQL:
    read-only    the connection uses the restricted database account, so the
                 database itself refuses writes; no SQL inspection involved
    timeout      the database cancels the statement after `timeout_seconds`
    row limit    at most `max_rows` rows are fetched; `truncated` says if more existed

A failing query is an expected outcome (the SQL may come from a model), so
errors are returned in the result, not raised.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

import oracledb
import psycopg
from psycopg import sql as pg_sql

from warehouse_scout.config import Settings
from warehouse_scout.db import connect_oracle, connect_postgres
from warehouse_scout.sqlutil import checked_identifier

Database = Literal["source", "target"]

DEFAULT_MAX_ROWS = 1_000
DEFAULT_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...] = ()
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None
    error_code: str | None = None  # ORA-nnnnn for Oracle, SQLSTATE for PostgreSQL

    @property
    def ok(self) -> bool:
        return self.error is None


def run_readonly(
    settings: Settings,
    database: Database,
    sql: str,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> QueryResult:
    if database == "source":
        return _run_on_oracle(settings, sql, max_rows, timeout_seconds)
    return _run_on_postgres(settings, sql, max_rows, timeout_seconds)


def _fetch(cursor: Any, max_rows: int) -> QueryResult:
    """Read up to `max_rows` rows from a cursor that has just executed a statement."""
    if cursor.description is None:  # the statement produced no result set
        return QueryResult()
    columns = tuple(column[0].lower() for column in cursor.description)
    # Ask for one row more than allowed: if it arrives, the result was cut short.
    rows = cursor.fetchmany(max_rows + 1)
    return QueryResult(columns=columns, rows=rows[:max_rows], truncated=len(rows) > max_rows)


def _run_on_oracle(
    settings: Settings, sql: str, max_rows: int, timeout_seconds: float
) -> QueryResult:
    try:
        with connect_oracle(settings, readonly=True) as connection:
            connection.call_timeout = int(timeout_seconds * 1000)  # milliseconds
            with connection.cursor() as cursor:
                # Unqualified table names resolve in the warehouse schema.
                schema = checked_identifier(settings.oracle_user)
                cursor.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {schema}")
                cursor.execute(sql.strip().rstrip(";"))  # Oracle rejects a trailing ";"
                return _fetch(cursor, max_rows)
    except oracledb.Error as error:
        (details,) = error.args
        # Oracle appends a "Help: <url>" line to every message; keep the first line.
        message = str(details.message).splitlines()[0]
        return QueryResult(error=message, error_code=details.full_code)


def _run_on_postgres(
    settings: Settings, sql: str, max_rows: int, timeout_seconds: float
) -> QueryResult:
    try:
        with connect_postgres(settings, readonly=True) as connection:
            connection.execute(
                pg_sql.SQL("SET statement_timeout = {}").format(
                    pg_sql.Literal(int(timeout_seconds * 1000))
                )
            )
            with connection.cursor() as cursor:
                # The SQL is passed whole, with no parameters: a "%" in it stays a "%".
                cursor.execute(pg_sql.SQL(sql))
                return _fetch(cursor, max_rows)
    except psycopg.Error as error:
        message = error.diag.message_primary or str(error)
        return QueryResult(error=message, error_code=error.sqlstate)
