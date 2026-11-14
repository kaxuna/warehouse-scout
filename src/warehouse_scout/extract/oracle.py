"""Read the source schema from Oracle's catalog: tables, views, functions.

Uses the read-only account and only catalog views (ALL_*), plus one COUNT(*)
per table. This is the "metadata access" a real client grants first: Scout
learns what exists without reading business data.
"""

from dataclasses import dataclass

import oracledb

from warehouse_scout.sqlutil import checked_identifier


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str  # formatted, e.g. "NUMBER(7,2)"
    nullable: bool


@dataclass(frozen=True)
class SchemaObject:
    name: str
    object_type: str  # "TABLE", "VIEW" or "FUNCTION"
    comment: str | None = None
    row_count: int | None = None
    definition: str | None = None
    columns: tuple[ColumnInfo, ...] = ()


def format_data_type(
    data_type: str, precision: int | None, scale: int | None, char_length: int | None
) -> str:
    """Oracle's catalog splits a type over several columns; put it back together."""
    if data_type == "NUMBER" and precision is not None:
        return f"NUMBER({precision},{scale})" if scale else f"NUMBER({precision})"
    if data_type in {"VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"} and char_length:
        return f"{data_type}({char_length})"
    return data_type


def _columns_by_object(cursor: oracledb.Cursor, owner: str) -> dict[str, list[ColumnInfo]]:
    cursor.execute(
        "SELECT table_name, column_name, data_type, data_precision, data_scale, char_length, "
        "nullable FROM all_tab_columns WHERE owner = :owner ORDER BY table_name, column_id",
        owner=owner,
    )
    columns: dict[str, list[ColumnInfo]] = {}
    for table, name, data_type, precision, scale, char_length, nullable in cursor:
        info = ColumnInfo(
            name, format_data_type(data_type, precision, scale, char_length), nullable == "Y"
        )
        columns.setdefault(table, []).append(info)
    return columns


def _comments(cursor: oracledb.Cursor, owner: str) -> dict[str, str]:
    cursor.execute(
        "SELECT table_name, comments FROM all_tab_comments "
        "WHERE owner = :owner AND comments IS NOT NULL",
        owner=owner,
    )
    return dict(cursor.fetchall())


def _row_count(cursor: oracledb.Cursor, owner: str, table: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {checked_identifier(owner)}.{checked_identifier(table)}")
    (count,) = cursor.fetchone()
    return int(count)


def extract_schema(ora: oracledb.Connection, owner: str) -> list[SchemaObject]:
    """Every table, view and function owned by `owner`, sorted by type and name."""
    owner = owner.upper()
    with ora.cursor() as cursor:
        columns = _columns_by_object(cursor, owner)
        comments = _comments(cursor, owner)

        cursor.execute(
            "SELECT table_name FROM all_tables WHERE owner = :owner ORDER BY table_name",
            owner=owner,
        )
        table_names = [name for (name,) in cursor.fetchall()]
        tables = [
            SchemaObject(
                name=name,
                object_type="TABLE",
                comment=comments.get(name),
                row_count=_row_count(cursor, owner, name),
                columns=tuple(columns.get(name, ())),
            )
            for name in table_names
        ]

        cursor.execute(
            "SELECT view_name, text FROM all_views WHERE owner = :owner ORDER BY view_name",
            owner=owner,
        )
        views = [
            SchemaObject(
                name=name,
                object_type="VIEW",
                comment=comments.get(name),
                definition=text.strip(),
                columns=tuple(columns.get(name, ())),
            )
            for name, text in cursor.fetchall()
        ]

        # ALL_SOURCE holds one row per line of code; put each function back together.
        cursor.execute(
            "SELECT name, text FROM all_source WHERE owner = :owner AND type = 'FUNCTION' "
            "ORDER BY name, line",
            owner=owner,
        )
        source_lines: dict[str, list[str]] = {}
        for name, text in cursor:
            source_lines.setdefault(name, []).append(text)
        functions = [
            SchemaObject(name=name, object_type="FUNCTION", definition="".join(lines).strip())
            for name, lines in source_lines.items()
        ]

    return [*tables, *views, *functions]
