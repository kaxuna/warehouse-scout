"""Search the index: nearest chunks to a question, optionally filtered by kind."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

from warehouse_scout.rag.embedder import Embedder
from warehouse_scout.rag.index import vector_literal

KINDS = ("table", "view", "function", "query")
DEFAULT_LIMIT = 5


@dataclass(frozen=True)
class SearchHit:
    kind: str
    object_name: str
    content: str
    metadata: dict[str, Any]
    score: float  # cosine similarity: 1.0 means same direction, 0 unrelated


def search(
    pg: psycopg.Connection[Any],
    embedder: Embedder,
    question: str,
    *,
    kinds: Sequence[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[SearchHit]:
    """The `limit` chunks closest in meaning to `question`, best first.

    `kinds` is a metadata filter: it narrows the candidates in SQL before
    similarity is ranked, e.g. only functions.
    """
    question_vector = vector_literal(embedder.embed_query(question))
    # `<=>` is pgvector's cosine distance (0 = same direction). The embedder
    # filter keeps vectors of other models out of the comparison.
    rows = pg.execute(
        """
        SELECT kind, object_name, content, metadata, 1 - (embedding <=> %(q)s::vector) AS score
        FROM scout.chunk
        WHERE embedder = %(embedder)s
          AND (%(kinds)s::text[] IS NULL OR kind = ANY(%(kinds)s::text[]))
        ORDER BY embedding <=> %(q)s::vector
        LIMIT %(limit)s
        """,
        {
            "q": question_vector,
            "embedder": embedder.name,
            "kinds": list(kinds) if kinds else None,
            "limit": limit,
        },
    ).fetchall()
    return [SearchHit(*row) for row in rows]
