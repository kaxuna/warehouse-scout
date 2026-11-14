"""Duplicate-detection eval: found pairs against the planted answer key.

Two methods per kind of object:
    exact      tables: same columns, count and rows; functions: same outputs
               on sample inputs
    embedding  pairs whose description vectors are closer than a threshold

The embedding rows use the threshold that maximises F1 *on this same data*,
which flatters them: a threshold picked in advance would do worse. They are
there to show what similarity of descriptions can and cannot tell.

    make eval-duplicates
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_postgres
from warehouse_scout.duplicates.functions import find_duplicate_functions
from warehouse_scout.duplicates.pairs import Pair, pairs_of, precision_recall
from warehouse_scout.duplicates.tables import find_duplicate_tables, rows_equal_on_source
from warehouse_scout.evals.translation import append_history, current_commit
from warehouse_scout.ingest.noise_tables import MANIFEST_PATH
from warehouse_scout.rag.embedder import HashEmbedder
from warehouse_scout.rag.index import load_schema

HISTORY_PATH = Path("evals/history/duplicates.jsonl")

# Names under which each embedder stores its vectors. Listed here so the eval
# can read stored vectors without loading the neural model.
EMBEDDER_NAMES = {
    "fastembed": "fastembed:BAAI/bge-small-en-v1.5",
    "hash": HashEmbedder.name,
}


def expected_pairs(manifest: dict[str, Any]) -> dict[str, set[Pair]]:
    return {
        "table": pairs_of(manifest["duplicate_table_groups"]),
        "function": pairs_of(manifest["duplicate_function_groups"].values()),
    }


def embedding_similarities(
    pg: psycopg.Connection[Any], embedder_name: str, kind: str
) -> list[tuple[Pair, float]]:
    """Cosine similarity of every pair of chunks of one kind."""
    rows = pg.execute(
        """
        SELECT a.object_name, b.object_name, 1 - (a.embedding <=> b.embedding)
        FROM scout.chunk a JOIN scout.chunk b ON a.id < b.id
        WHERE a.embedder = %(embedder)s AND b.embedder = %(embedder)s
          AND a.kind = %(kind)s AND b.kind = %(kind)s
        """,
        {"embedder": embedder_name, "kind": kind},
    ).fetchall()
    return [(frozenset((first, second)), float(similarity)) for first, second, similarity in rows]


def best_threshold(
    similarities: Sequence[tuple[Pair, float]], expected: set[Pair]
) -> tuple[float, dict[str, float]]:
    """The similarity threshold with the highest F1, and its scores."""
    best: tuple[float, dict[str, float]] = (1.0, precision_recall(set(), expected))
    for threshold in sorted({round(similarity, 2) for _, similarity in similarities}):
        predicted = {pair for pair, similarity in similarities if similarity >= threshold}
        scores = precision_recall(predicted, expected)
        if scores["f1"] > best[1]["f1"]:
            best = (threshold, scores)
    return best


def format_row(kind: str, method: str, scores: dict[str, float]) -> str:
    return (
        f"| {kind} | {method} | {scores['predicted']:.0f} | {scores['correct']:.0f} "
        f"| {scores['precision']:.0%} | {scores['recall']:.0%} | {scores['f1']:.2f} |"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the duplicate-detection eval.")
    parser.add_argument("--no-history", action="store_true", help="do not record this run")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    expected = expected_pairs(json.loads(MANIFEST_PATH.read_text()))
    # Only the embedder's name is needed: the vectors are already in the index.
    embedder_name = EMBEDDER_NAMES[settings.scout_embedder]

    with connect_postgres(settings) as pg:
        objects = load_schema(pg)
        exact = {
            "table": find_duplicate_tables(objects, rows_equal_on_source(settings)),
            "function": find_duplicate_functions(settings),
        }
        rows: list[tuple[str, str, dict[str, float]]] = []
        for kind in ("table", "function"):
            rows.append((kind, "exact", precision_recall(exact[kind], expected[kind])))
            threshold, scores = best_threshold(
                embedding_similarities(pg, embedder_name, kind), expected[kind]
            )
            rows.append((kind, f"embedding >= {threshold:.2f} (best)", scores))

    print(
        f"Answer key: {len(expected['table'])} duplicate table pairs, "
        f"{len(expected['function'])} duplicate function pairs\n"
    )
    print("| Kind | Method | Predicted pairs | Correct | Precision | Recall | F1 |")
    print("|---|---|---|---|---|---|---|")
    for kind, method, scores in rows:
        print(format_row(kind, method, scores))

    for kind in ("table", "function"):
        wrong = sorted(sorted(pair) for pair in exact[kind] - expected[kind])
        missed = sorted(sorted(pair) for pair in expected[kind] - exact[kind])
        if wrong or missed:
            print(f"\n{kind}, exact method: wrong {wrong[:10]}, missed {missed[:10]}")

    if not args.no_history:
        for kind, method, scores in rows:
            append_history(
                {
                    "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "commit": current_commit(),
                    "kind": kind,
                    "method": method,
                    **scores,
                },
                HISTORY_PATH,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
