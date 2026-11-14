"""Build the search index: chunk the snapshot, embed the chunks, store the vectors.

    make index                          # local neural embedder
    make index ARGS="--embedder hash"   # word-count embedder, for comparison

Run `make extract` first. Each embedder's vectors are stored side by side.
"""

import argparse
import sys
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_postgres
from warehouse_scout.extract.oracle import ColumnInfo, SchemaObject
from warehouse_scout.rag.chunks import Chunk, build_chunks, query_shapes
from warehouse_scout.rag.embedder import Embedder, Vector, make_embedder
from warehouse_scout.store.snapshot import ensure_schema

EMBED_BATCH_SIZE = 64


def vector_literal(vector: Vector) -> str:
    """A vector in pgvector's text form: [0.1,0.2,...]."""
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


def load_schema(pg: psycopg.Connection[Any]) -> list[SchemaObject]:
    """Read the stored schema snapshot back into objects."""
    columns: dict[str, list[ColumnInfo]] = {}
    for name, column_name, data_type, nullable in pg.execute(
        "SELECT object_name, column_name, data_type, nullable FROM scout.schema_column "
        "ORDER BY object_name, position"
    ):
        columns.setdefault(name, []).append(ColumnInfo(column_name, data_type, nullable))

    return [
        SchemaObject(
            name, object_type, comment, row_count, definition, tuple(columns.get(name, ()))
        )
        for name, object_type, comment, row_count, definition in pg.execute(
            "SELECT object_name, object_type, comment, row_count, definition "
            "FROM scout.schema_object ORDER BY object_type, object_name"
        )
    ]


def load_chunks(pg: psycopg.Connection[Any]) -> list[Chunk]:
    log_rows = pg.execute("SELECT sql, user_name, elapsed_ms FROM scout.query_log")
    return build_chunks(load_schema(pg), query_shapes(log_rows))


def store_chunks(pg: psycopg.Connection[Any], embedder: Embedder, chunks: Sequence[Chunk]) -> None:
    """Embed `chunks` and replace this embedder's rows with them."""
    rows: list[tuple[Any, ...]] = []
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        vectors = embedder.embed([chunk.content for chunk in batch])
        rows.extend(
            (
                embedder.name,
                chunk.kind,
                chunk.object_name,
                chunk.content,
                Jsonb(chunk.metadata),
                vector_literal(vector),
            )
            for chunk, vector in zip(batch, vectors, strict=True)
        )

    with pg.cursor() as cursor:
        cursor.execute("DELETE FROM scout.chunk WHERE embedder = %s", [embedder.name])
        cursor.executemany(
            "INSERT INTO scout.chunk (embedder, kind, object_name, content, metadata, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s::vector)",
            rows,
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the search index.")
    parser.add_argument("--embedder", choices=["fastembed", "hash"])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    embedder = make_embedder(args.embedder or settings.scout_embedder)

    with connect_postgres(settings) as pg:
        ensure_schema(pg)
        chunks = load_chunks(pg)
        if not chunks:
            print("Nothing to index. Run `make extract` first.", file=sys.stderr)
            return 1
        store_chunks(pg, embedder, chunks)

    by_kind = {
        kind: sum(c.kind == kind for c in chunks) for kind in ("table", "view", "function", "query")
    }
    print(f"Indexed {len(chunks)} chunks with {embedder.name}: {by_kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
