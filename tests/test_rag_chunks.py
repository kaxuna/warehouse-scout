"""Tests for chunking, query fingerprints and the embedder used in tests."""

import math

import pytest

from warehouse_scout.extract.oracle import ColumnInfo, SchemaObject
from warehouse_scout.ingest.noise_catalog import NOISE_TABLES
from warehouse_scout.ingest.query_log import generate_log
from warehouse_scout.ingest.query_templates import QUERY_TEMPLATES
from warehouse_scout.rag.chunks import (
    QueryShape,
    build_chunks,
    count_references,
    fingerprint,
    object_chunk,
    query_shapes,
)
from warehouse_scout.rag.embedder import Embedder, HashEmbedder, make_embedder
from warehouse_scout.rag.index import vector_literal


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# --- Fingerprints ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT a FROM t WHERE y = 2001", "SELECT a FROM t WHERE y = ?"),
        ("SELECT a FROM t WHERE s = 'TN'", "SELECT a FROM t WHERE s = ?"),
        ("SELECT a FROM t WHERE s = 'O''Brien'", "SELECT a FROM t WHERE s = ?"),
        ("SELECT a\n  FROM   t", "SELECT a FROM t"),
        ("SELECT price * 1.25 FROM t", "SELECT price * ? FROM t"),
        ("SELECT * FROM v_customer_360", "SELECT * FROM v_customer_360"),  # digits in a name
        ("SELECT q1 FROM t WHERE ROWNUM <= 10", "SELECT q1 FROM t WHERE ROWNUM <= ?"),
    ],
)
def test_fingerprint(sql: str, expected: str) -> None:
    assert fingerprint(sql) == expected


def test_log_collapses_to_exactly_one_shape_per_template() -> None:
    log = generate_log(days=30)
    shapes = query_shapes((entry.sql, entry.user, entry.elapsed_ms) for entry in log)

    assert len({entry.sql for entry in log}) > 200  # many distinct statements ...
    assert len(shapes) == len(QUERY_TEMPLATES)  # ... but only this many shapes
    assert sum(shape.runs for shape in shapes) == len(log)


def test_shapes_are_sorted_by_runs_and_aggregate_users_and_time() -> None:
    rows = [
        ("SELECT 1 FROM t WHERE a = 5", "ann", 100),
        ("SELECT 1 FROM t WHERE a = 7", "bob", 300),
        ("SELECT 2 FROM u", "ann", 50),
    ]

    assert query_shapes(rows) == [
        QueryShape("SELECT ? FROM t WHERE a = ?", runs=2, users=("ann", "bob"), average_ms=200),
        QueryShape("SELECT ? FROM u", runs=1, users=("ann",), average_ms=50),
    ]


# --- Usage counts: the dead-table signal -----------------------------------------


def test_references_match_whole_words_only() -> None:
    shapes = [
        QueryShape("SELECT * FROM dim_item WHERE ss_item_sk = ?", 5, ("a",), 1),
        QueryShape("select * from ITEM", 2, ("a",), 1),
    ]

    assert count_references("ITEM", shapes) == 2
    assert count_references("DIM_ITEM", shapes) == 5
    assert count_references("ITEM2", shapes) == 0


def test_usage_count_separates_dead_tables_from_active_ones() -> None:
    log = generate_log(days=30)
    shapes = query_shapes((entry.sql, entry.user, entry.elapsed_ms) for entry in log)

    runs = {table.name: count_references(table.name, shapes) for table in NOISE_TABLES}

    assert all(runs[t.name] == 0 for t in NOISE_TABLES if t.status == "dead")
    assert all(runs[t.name] > 0 for t in NOISE_TABLES if t.status == "active")


# --- Chunks -----------------------------------------------------------------------


def test_table_chunk_reads_like_a_description() -> None:
    table = SchemaObject(
        "ITEM",
        "TABLE",
        comment="Products with prices.",
        row_count=1800,
        columns=(
            ColumnInfo("I_ITEM_SK", "NUMBER(19)", False),
            ColumnInfo("I_BRAND", "VARCHAR2(50)", True),
        ),
    )
    shapes = [QueryShape("SELECT i_brand FROM item", 7, ("a",), 1)]

    chunk = object_chunk(table, shapes)

    assert (chunk.kind, chunk.object_name) == ("table", "ITEM")
    assert chunk.content == "Table ITEM. Products with prices. Columns: i_item_sk, i_brand."
    assert chunk.metadata == {"query_runs": 7, "row_count": 1800}


def test_function_chunk_carries_its_source() -> None:
    function = SchemaObject("GET_QTR", "FUNCTION", definition="FUNCTION get_qtr ... END;")

    chunk = object_chunk(function, [])

    assert chunk.content == "Function GET_QTR. Definition: FUNCTION get_qtr ... END;"
    assert chunk.metadata == {"query_runs": 0}


def test_build_chunks_numbers_query_shapes_by_frequency() -> None:
    shapes = [
        QueryShape("SELECT ? FROM t", 9, ("a",), 3),
        QueryShape("SELECT ? FROM u", 1, ("b",), 4),
    ]

    chunks = build_chunks([SchemaObject("T", "TABLE")], shapes)

    assert [(c.kind, c.object_name) for c in chunks] == [
        ("table", "T"),
        ("query", "Q001"),
        ("query", "Q002"),
    ]
    assert chunks[1].metadata == {"runs": 9, "users": ["a"], "average_ms": 3}


# --- Embedders --------------------------------------------------------------------


def test_hash_embedder_satisfies_the_protocol_and_is_deterministic() -> None:
    embedder: Embedder = HashEmbedder()

    first, second = embedder.embed(["store sales revenue"]), embedder.embed(["store sales revenue"])

    assert first == second
    assert len(first[0]) == embedder.dimensions == 256
    assert math.isclose(dot(first[0], first[0]), 1.0)  # unit length


def test_hash_embedder_scores_shared_words_higher() -> None:
    question, related, unrelated = HashEmbedder().embed(
        ["customer address state", "Table CUSTOMER_ADDRESS. Columns: ca_state", "Table TIME_DIM"]
    )

    assert dot(question, related) > dot(question, unrelated)


def test_hash_embedder_handles_text_without_words() -> None:
    assert HashEmbedder(dimensions=8).embed(["!!!"]) == [[0.0] * 8]


def test_make_embedder_rejects_unknown_kinds() -> None:
    assert make_embedder("hash").name == "hash-bag-of-words"
    with pytest.raises(ValueError, match="klingon"):
        make_embedder("klingon")


def test_vector_literal_is_pgvector_text_format() -> None:
    assert vector_literal([0.5, -1.0, 0.123456789]) == "[0.500000,-1.000000,0.123457]"
