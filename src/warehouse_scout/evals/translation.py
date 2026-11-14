"""Translation eval: does a translated query return the same rows as the original?

For every read query in the workload:
    1. run the original on Oracle             -> expected rows
    2. translate it with the system under test
    3. run the translation on PostgreSQL      -> actual rows
    4. compare

A case passes only on "match". The score is deterministic: no judge model,
no human labels. Any `Translator` can be the system under test, which is how
"sqlglot alone" and "agent" end up as two rows of the same table.

    make eval-translation
"""

import argparse
import json
import random
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from warehouse_scout.compare import compare_results
from warehouse_scout.config import Settings, get_settings
from warehouse_scout.ingest.query_log import render
from warehouse_scout.ingest.query_templates import QUERY_TEMPLATES
from warehouse_scout.translate import SqlglotTranslator, Translator
from warehouse_scout.warehouse import run_readonly

HISTORY_PATH = Path("evals/history/translation.jsonl")
CASE_SEED = 1  # fixed, so every run evaluates the same literal values

# Generous limits: the eval must see whole results to compare them.
MAX_ROWS = 100_000
TIMEOUT_SECONDS = 60.0

SYSTEMS: dict[str, type[Translator]] = {"sqlglot": SqlglotTranslator}


@dataclass(frozen=True)
class Case:
    id: str
    sql: str
    features: tuple[str, ...]


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    status: str  # a compare.Status value, or "translate_error"
    detail: str
    source_rows: int | None = None
    target_rows: int | None = None
    translated_sql: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "match"


def build_cases(seed: int = CASE_SEED) -> list[Case]:
    """Read queries from the workload, with literals filled in reproducibly.

    Left out: statements that change data (the eval is read-only) and the
    planted wasteful queries (a cartesian join is not worth waiting for).
    """
    rng = random.Random(seed)
    cases = []
    for template in QUERY_TEMPLATES:
        sql = render(template, rng)  # always render, so the seed sequence is stable
        is_read_query = sql.lstrip().upper().startswith(("SELECT", "WITH"))
        if is_read_query and template.waste is None:
            cases.append(Case(id=template.id, sql=sql, features=template.features))
    return cases


def run_case(settings: Settings, translator: Translator, case: Case) -> CaseResult:
    translation = translator.translate(case.sql, source="oracle", target="postgres")
    if translation.sql is None:
        return CaseResult(case.id, "translate_error", translation.error or "no output")

    source = run_readonly(
        settings, "source", case.sql, max_rows=MAX_ROWS, timeout_seconds=TIMEOUT_SECONDS
    )
    target = run_readonly(
        settings, "target", translation.sql, max_rows=MAX_ROWS, timeout_seconds=TIMEOUT_SECONDS
    )
    comparison = compare_results(source, target)

    return CaseResult(
        case_id=case.id,
        status=comparison.status,
        detail=comparison.detail,
        source_rows=comparison.source_rows,
        target_rows=comparison.target_rows,
        translated_sql=translation.sql,
        warnings=translation.warnings,
    )


def summarize(results: Sequence[CaseResult]) -> dict[str, object]:
    passed = [result for result in results if result.passed]
    return {
        "total": len(results),
        "passed": len(passed),
        "pass_rate": round(len(passed) / len(results), 4) if results else 0.0,
        "by_status": dict(Counter(result.status for result in results).most_common()),
        # A match on zero rows proves little: both sides returned nothing.
        "passed_with_zero_rows": sum(result.source_rows == 0 for result in passed),
    }


def format_table(results: Sequence[CaseResult]) -> str:
    """Results as a Markdown table, failures first."""
    lines = ["| Case | Status | Rows (source / target) | Detail |", "|---|---|---|---|"]
    for result in sorted(results, key=lambda r: (r.passed, r.status, r.case_id)):
        rows = "" if result.source_rows is None else f"{result.source_rows} / {result.target_rows}"
        detail = result.detail.replace("|", "\\|").replace("\n", " ")[:90]
        lines.append(f"| {result.case_id} | {result.status} | {rows} | {detail} |")
    return "\n".join(lines)


def current_commit() -> str | None:
    """Short hash of the checked-out commit, or None outside a git checkout."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def history_record(system: str, results: Sequence[CaseResult]) -> dict[str, object]:
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "system": system,
        "commit": current_commit(),
        **summarize(results),
        "failures": {result.case_id: result.status for result in results if not result.passed},
    }


def append_history(record: dict[str, object], path: Path = HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as file:
        file.write(json.dumps(record) + "\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the translation eval.")
    parser.add_argument("--system", choices=sorted(SYSTEMS), default="sqlglot")
    parser.add_argument("--no-history", action="store_true", help="do not record this run")
    parser.add_argument("--details", type=Path, help="write full per-case results as JSON here")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    translator = SYSTEMS[args.system]()

    results = [run_case(settings, translator, case) for case in build_cases()]
    summary = summarize(results)

    print(format_table(results))
    print()
    print(
        f"{args.system}: {summary['passed']} of {summary['total']} cases pass "
        f"({summary['pass_rate']:.0%}); {summary['passed_with_zero_rows']} of the passes "
        "compared zero rows."
    )
    print(f"by status: {summary['by_status']}")

    if args.details:
        args.details.parent.mkdir(parents=True, exist_ok=True)
        args.details.write_text(json.dumps([asdict(r) for r in results], indent=2) + "\n")
    if not args.no_history:
        append_history(history_record(args.system, results))
        print(f"Recorded in {HISTORY_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
