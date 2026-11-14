"""The eval gate: run the evals that need no API key and fail if a score dropped.

    make eval-gate

Each metric has a minimum in `evals/thresholds.json`. The command exits with
status 1 if any metric is below its minimum or missing, which is what makes
a CI job fail. A change that makes search or translation worse is then caught
by the pull request that causes it, like a failing unit test.

Raising a minimum after an improvement is a deliberate edit to that file, so
the bar only moves when someone decides it should.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from warehouse_scout.config import Settings, get_settings
from warehouse_scout.db import connect_postgres
from warehouse_scout.duplicates.functions import find_duplicate_functions
from warehouse_scout.duplicates.pairs import precision_recall
from warehouse_scout.duplicates.tables import find_duplicate_tables, rows_equal_on_source
from warehouse_scout.evals import retrieval, translation
from warehouse_scout.evals.duplicates import expected_pairs
from warehouse_scout.ingest.noise_tables import MANIFEST_PATH
from warehouse_scout.rag.embedder import FastEmbedEmbedder
from warehouse_scout.rag.index import load_schema
from warehouse_scout.translate import SqlglotTranslator

THRESHOLDS_PATH = Path("evals/thresholds.json")


@dataclass(frozen=True)
class GateResult:
    metric: str
    minimum: float
    value: float | None  # None: the metric was not produced at all

    @property
    def ok(self) -> bool:
        return self.value is not None and self.value >= self.minimum


def collect_metrics(settings: Settings) -> dict[str, float]:
    """Run every key-free eval and return its headline numbers."""
    translator = SqlglotTranslator()
    translated = [
        translation.run_case(settings, translator, case) for case in translation.build_cases()
    ]
    metrics = {
        "translation_sqlglot_pass_rate": float(str(translation.summarize(translated)["pass_rate"]))
    }

    expected = expected_pairs(json.loads(MANIFEST_PATH.read_text()))
    with connect_postgres(settings) as pg:
        ranks = retrieval.evaluate(
            pg, FastEmbedEmbedder(), retrieval.load_cases(), filter_by_kind=True
        )
        objects = load_schema(pg)
    search_scores = retrieval.score(ranks)
    metrics["retrieval_recall_at_5"] = search_scores["recall_at_5"]
    metrics["retrieval_mrr"] = search_scores["mrr"]

    tables = precision_recall(
        find_duplicate_tables(objects, rows_equal_on_source(settings)), expected["table"]
    )
    functions = precision_recall(find_duplicate_functions(settings), expected["function"])
    for name, scores in (("tables", tables), ("functions", functions)):
        metrics[f"duplicate_{name}_precision"] = scores["precision"]
        metrics[f"duplicate_{name}_recall"] = scores["recall"]
    return metrics


def check(metrics: dict[str, float], thresholds: dict[str, float]) -> list[GateResult]:
    """One result per threshold. A threshold without a metric fails: an eval
    that silently stopped reporting must not pass the gate."""
    return [
        GateResult(metric, minimum, metrics.get(metric))
        for metric, minimum in sorted(thresholds.items())
    ]


def format_results(results: list[GateResult]) -> str:
    lines = ["| Metric | Minimum | Value | |", "|---|---|---|---|"]
    for result in results:
        value = "missing" if result.value is None else f"{result.value:.4f}"
        verdict = "ok" if result.ok else "BELOW MINIMUM"
        lines.append(f"| {result.metric} | {result.minimum:.4f} | {value} | {verdict} |")
    return "\n".join(lines)


def main() -> int:
    thresholds = json.loads(THRESHOLDS_PATH.read_text())
    results = check(collect_metrics(get_settings()), thresholds)

    print(format_results(results))
    failed = [result.metric for result in results if not result.ok]
    if failed:
        print(f"\nEval gate FAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\nEval gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
