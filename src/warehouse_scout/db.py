"""Database connection helpers.

Both functions return a live connection. Use them with `with`, which closes the
connection when the block exits, even on an exception (Java: try-with-resources):

    with connect_oracle(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM dual")

`readonly=True` connects as the restricted account. Everything the agent runs
must go through a read-only connection; the loader is the only writer.
"""

from typing import Any

import oracledb
import psycopg

from warehouse_scout.config import Settings


def connect_postgres(
    settings: Settings, *, readonly: bool = False
) -> psycopg.Connection[tuple[Any, ...]]:
    user = settings.postgres_ro_user if readonly else settings.postgres_user
    password = settings.postgres_ro_password if readonly else settings.postgres_password
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=user,
        password=password.get_secret_value(),
    )


def connect_oracle(settings: Settings, *, readonly: bool = False) -> oracledb.Connection:
    user = settings.oracle_ro_user if readonly else settings.oracle_user
    password = settings.oracle_ro_password if readonly else settings.oracle_password
    return oracledb.connect(
        user=user,
        password=password.get_secret_value(),
        dsn=settings.oracle_dsn,
    )
