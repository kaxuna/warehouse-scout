"""Tests for the pure parts of extraction."""

from pathlib import Path

import pytest

from warehouse_scout.extract.oracle import format_data_type
from warehouse_scout.store.snapshot import SCHEMA_SQL, main


@pytest.mark.parametrize(
    ("data_type", "precision", "scale", "char_length", "expected"),
    [
        ("NUMBER", 7, 2, None, "NUMBER(7,2)"),
        ("NUMBER", 19, 0, None, "NUMBER(19)"),
        ("NUMBER", None, None, None, "NUMBER"),
        ("VARCHAR2", None, None, 4000, "VARCHAR2(4000)"),
        ("CHAR", None, None, 1, "CHAR(1)"),
        ("DATE", None, None, 0, "DATE"),
    ],
)
def test_format_data_type(
    data_type: str, precision: int | None, scale: int | None, char_length: int | None, expected: str
) -> None:
    assert format_data_type(data_type, precision, scale, char_length) == expected


def test_schema_sql_is_safe_to_run_twice() -> None:
    statements = [s for s in SCHEMA_SQL.read_text().split(";") if "CREATE" in s]

    assert statements
    assert all("IF NOT EXISTS" in statement for statement in statements)


def test_missing_query_log_is_reported_before_any_connection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--query-log", str(tmp_path / "absent.jsonl")])

    assert exit_code == 1
    assert "make querylog" in capsys.readouterr().err
