"""The index in PostgreSQL, and the local neural embedder. Needs `make extract` first."""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_postgres
from warehouse_scout.rag.embedder import Embedder, FastEmbedEmbedder, HashEmbedder
from warehouse_scout.rag.index import load_chunks, store_chunks

pytestmark = pytest.mark.integration


@pytest.fixture
def pg() -> Iterator[psycopg.Connection[Any]]:
    with connect_postgres(get_settings()) as connection:
        yield connection
        connection.rollback()


def test_chunks_cover_every_object_and_query_shape(pg: psycopg.Connection[Any]) -> None:
    chunks = load_chunks(pg)
    kinds = [chunk.kind for chunk in chunks]

    assert (kinds.count("table"), kinds.count("view"), kinds.count("function")) == (126, 4, 12)
    assert kinds.count("query") == 56


def test_store_replaces_only_its_own_embedders_rows(pg: psycopg.Connection[Any]) -> None:
    embedder = HashEmbedder(dimensions=32)
    embedder.name = "test-embedder"
    chunks = load_chunks(pg)[:10]
    before = pg.execute("SELECT COUNT(*) FROM scout.chunk").fetchone()

    store_chunks(pg, embedder, chunks)
    store_chunks(pg, embedder, chunks)

    after = pg.execute("SELECT COUNT(*) FROM scout.chunk").fetchone()
    mine = pg.execute(
        "SELECT COUNT(*), MIN(vector_dims(embedding)) FROM scout.chunk WHERE embedder = %s",
        [embedder.name],
    ).fetchone()
    assert before is not None and after is not None
    assert after[0] == before[0] + 10
    assert mine == (10, 32)


def test_neural_embedder_puts_related_texts_closer() -> None:
    embedder: Embedder = FastEmbedEmbedder()  # downloads the model on first use

    question, related, unrelated = embedder.embed(
        [
            "how much do products cost?",
            "Table ITEM. Dimension: products with brand, class, category and prices.",
            "Table EMPLOYEE. Staff directory.",
        ]
    )

    def dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert len(question) == embedder.dimensions
    assert dot(question, question) == pytest.approx(1.0, abs=1e-3)
    # No word is shared between the question and the ITEM text; meaning is.
    assert dot(question, related) > dot(question, unrelated)
