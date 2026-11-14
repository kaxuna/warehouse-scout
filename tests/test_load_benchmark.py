"""Spec for the pure functions of the loader. No database needed.

Every test is marked xfail while the functions are stubs, so CI stays green.
When your implementation passes, delete the `pytestmark` line below.
"""

import pytest

from warehouse_scout.ingest.load_benchmark import Column, create_table_ddl, oracle_type

pytestmark = pytest.mark.xfail(raises=NotImplementedError, reason="exercise not written yet")


@pytest.mark.parametrize(
    ("duckdb_type", "expected"),
    [
        ("INTEGER", "NUMBER(10)"),
        ("BIGINT", "NUMBER(19)"),
        ("DECIMAL(7,2)", "NUMBER(7,2)"),
        ("DECIMAL(15,2)", "NUMBER(15,2)"),
        ("VARCHAR", "VARCHAR2(4000)"),
        ("DATE", "DATE"),
    ],
)
def test_oracle_type_maps_every_tpcds_type(duckdb_type: str, expected: str) -> None:
    assert oracle_type(duckdb_type) == expected


def test_oracle_type_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="HUGEINT"):
        oracle_type("HUGEINT")


def test_create_table_ddl() -> None:
    columns = [
        Column(name="i_item_sk", duckdb_type="BIGINT", nullable=False),
        Column(name="i_item_desc", duckdb_type="VARCHAR", nullable=True),
        Column(name="i_current_price", duckdb_type="DECIMAL(7,2)", nullable=True),
    ]

    ddl = create_table_ddl("item", columns)

    # Whitespace and letter case are yours to choose; compare normalized.
    assert " ".join(ddl.upper().split()) == (
        "CREATE TABLE ITEM ("
        "I_ITEM_SK NUMBER(19) NOT NULL, "
        "I_ITEM_DESC VARCHAR2(4000), "
        "I_CURRENT_PRICE NUMBER(7,2))"
    )
