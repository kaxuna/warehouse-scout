"""Tests for the eval runner's pure parts: case selection, scoring, reporting."""

import json
from pathlib import Path

from warehouse_scout.evals.translation import (
    Case,
    CaseResult,
    append_history,
    build_cases,
    format_table,
    history_record,
    run_case,
    summarize,
)
from warehouse_scout.translate import Translation

RESULTS = [
    CaseResult("a_good", "match", "same rows", 10, 10),
    CaseResult("b_empty", "match", "same rows", 0, 0),
    CaseResult("c_wrong", "mismatch", "1 of 3 rows differ", 3, 3),
    CaseResult("d_broken", "target_error", '42601: syntax error at or near "BY" | here'),
]


def test_cases_are_read_queries_without_the_wasteful_ones() -> None:
    cases = build_cases()
    ids = {case.id for case in cases}

    assert all(case.sql.lstrip().upper().startswith(("SELECT", "WITH")) for case in cases)
    assert "org_chart" in ids
    assert "etl_merge_dim_customer" not in ids
    assert "waste_cartesian_join" not in ids


def test_cases_are_reproducible() -> None:
    assert build_cases() == build_cases()
    assert all("{" not in case.sql for case in build_cases())


def test_summarize_counts_only_matches_as_passes() -> None:
    assert summarize(RESULTS) == {
        "total": 4,
        "passed": 2,
        "pass_rate": 0.5,
        "by_status": {"match": 2, "mismatch": 1, "target_error": 1},
        "passed_with_zero_rows": 1,
    }


def test_summarize_handles_no_results() -> None:
    assert summarize([])["pass_rate"] == 0.0


def test_table_lists_failures_first_and_escapes_pipes() -> None:
    lines = format_table(RESULTS).splitlines()

    assert [line.split("|")[1].strip() for line in lines[2:]] == [
        "c_wrong",
        "d_broken",
        "a_good",
        "b_empty",
    ]
    assert "\\| here" in lines[3]
    assert "| 10 / 10 |" in lines[4]


def test_history_is_appended_one_json_object_per_line(tmp_path: Path) -> None:
    path = tmp_path / "history" / "runs.jsonl"

    append_history(history_record("sqlglot", RESULTS), path)
    append_history(history_record("agent", RESULTS[:1]), path)

    first, second = (json.loads(line) for line in path.read_text().splitlines())
    assert (first["system"], first["passed"], first["total"]) == ("sqlglot", 2, 4)
    assert first["failures"] == {"c_wrong": "mismatch", "d_broken": "target_error"}
    assert (second["system"], second["pass_rate"]) == ("agent", 1.0)


class FailingTranslator:
    name = "always-fails"

    def translate(self, sql: str, source: str, target: str) -> Translation:
        return Translation(sql=None, error="ParseError: nope")


def test_translation_failure_is_scored_without_touching_a_database() -> None:
    # settings is never used on this path, so None stands in for it.
    result = run_case(None, FailingTranslator(), Case("x", "SELECT 1", ()))  # type: ignore[arg-type]

    assert (result.status, result.detail, result.passed) == (
        "translate_error",
        "ParseError: nope",
        False,
    )
