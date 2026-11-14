"""Tests for the gate's decision logic. The evals themselves are not run here."""

import json

from warehouse_scout.evals.gate import THRESHOLDS_PATH, GateResult, check, format_results


def test_metric_at_or_above_its_minimum_passes() -> None:
    results = check({"recall": 0.91, "precision": 1.0}, {"recall": 0.85, "precision": 1.0})

    assert [result.ok for result in results] == [True, True]


def test_metric_below_its_minimum_fails() -> None:
    (result,) = check({"recall": 0.84}, {"recall": 0.85})

    assert not result.ok
    assert (result.value, result.minimum) == (0.84, 0.85)


def test_missing_metric_fails_instead_of_passing_silently() -> None:
    (result,) = check({}, {"recall": 0.85})

    assert result.value is None
    assert not result.ok


def test_metrics_without_a_threshold_are_ignored() -> None:
    assert check({"new_metric": 0.1}, {}) == []


def test_report_marks_failures() -> None:
    report = format_results(
        [
            GateResult("a_good", 0.5, 0.75),
            GateResult("b_low", 0.5, 0.25),
            GateResult("c_gone", 0.5, None),
        ]
    )

    lines = report.splitlines()[2:]
    assert lines[0].endswith("| ok |")
    assert lines[1].endswith("| BELOW MINIMUM |")
    assert "| missing | BELOW MINIMUM |" in lines[2]


def test_committed_thresholds_are_fractions() -> None:
    thresholds = json.loads(THRESHOLDS_PATH.read_text())

    assert thresholds
    assert all(0.0 < minimum <= 1.0 for minimum in thresholds.values())
