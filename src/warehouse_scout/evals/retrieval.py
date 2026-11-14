"""Retrieval eval: does search put the right object near the top?

Each case is a question someone might ask and the objects that answer it.
The questions avoid the objects' own words where they can ("shops", not
"store"), because matching identical words needs no embedding model.

Metrics:
    recall@1, recall@5   share of questions with a right answer in the top 1 / 5
    MRR                  mean of 1/rank of the first right answer (0 if absent);
                         rewards putting it first, not merely in the top 5

Run with and without the kind filter, to see what metadata filtering adds.

    make eval-retrieval
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_postgres
from warehouse_scout.evals.translation import append_history, current_commit
from warehouse_scout.rag.embedder import Embedder, make_embedder
from warehouse_scout.rag.search import SearchHit, search

GOLDEN_PATH = Path("evals/golden/retrieval.jsonl")
HISTORY_PATH = Path("evals/history/retrieval.jsonl")
TOP_K = 5


@dataclass(frozen=True)
class RetrievalCase:
    question: str
    kind: str
    expected_names: tuple[str, ...] = ()
    expected_text: tuple[str, ...] = ()  # for query shapes: a fragment of the SQL

    def is_answered_by(self, hit: SearchHit) -> bool:
        return hit.object_name in self.expected_names or any(
            fragment in hit.content for fragment in self.expected_text
        )


def load_cases(path: Path = GOLDEN_PATH) -> list[RetrievalCase]:
    cases = []
    for line in path.read_text().splitlines():
        record = json.loads(line)
        cases.append(
            RetrievalCase(
                question=record["question"],
                kind=record["kind"],
                expected_names=tuple(record.get("expected_names", ())),
                expected_text=tuple(record.get("expected_text", ())),
            )
        )
    return cases


def first_correct_rank(case: RetrievalCase, hits: Sequence[SearchHit]) -> int | None:
    """1-based position of the first right answer, or None if there is none."""
    for rank, hit in enumerate(hits, start=1):
        if case.is_answered_by(hit):
            return rank
    return None


def score(ranks: Sequence[int | None]) -> dict[str, float]:
    total = len(ranks)
    if total == 0:
        return {"recall_at_1": 0.0, "recall_at_5": 0.0, "mrr": 0.0}
    found = [rank for rank in ranks if rank is not None]
    return {
        "recall_at_1": round(sum(rank == 1 for rank in found) / total, 4),
        "recall_at_5": round(len(found) / total, 4),
        "mrr": round(sum(1 / rank for rank in found) / total, 4),
    }


def evaluate(
    pg: psycopg.Connection[Any],
    embedder: Embedder,
    cases: Sequence[RetrievalCase],
    *,
    filter_by_kind: bool,
) -> list[int | None]:
    return [
        first_correct_rank(
            case,
            search(
                pg,
                embedder,
                case.question,
                kinds=[case.kind] if filter_by_kind else None,
                limit=TOP_K,
            ),
        )
        for case in cases
    ]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the retrieval eval.")
    parser.add_argument("--embedders", nargs="+", default=["fastembed", "hash"])
    parser.add_argument("--no-history", action="store_true", help="do not record this run")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    cases = load_cases()

    print(f"{len(cases)} questions, top {TOP_K}\n")
    print("| Embedder | Kind filter | Recall@1 | Recall@5 | MRR |")
    print("|---|---|---|---|---|")

    with connect_postgres(get_settings()) as pg:
        for kind in args.embedders:
            embedder = make_embedder(kind)
            for filter_by_kind in (True, False):
                ranks = evaluate(pg, embedder, cases, filter_by_kind=filter_by_kind)
                metrics = score(ranks)
                print(
                    f"| {embedder.name} | {'yes' if filter_by_kind else 'no'} "
                    f"| {metrics['recall_at_1']:.0%} | {metrics['recall_at_5']:.0%} "
                    f"| {metrics['mrr']:.2f} |"
                )
                missed = [
                    case.question for case, rank in zip(cases, ranks, strict=True) if rank is None
                ]
                if not args.no_history:
                    append_history(
                        {
                            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                            "commit": current_commit(),
                            "embedder": embedder.name,
                            "filter_by_kind": filter_by_kind,
                            "cases": len(cases),
                            **metrics,
                            "missed": missed,
                        },
                        HISTORY_PATH,
                    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
