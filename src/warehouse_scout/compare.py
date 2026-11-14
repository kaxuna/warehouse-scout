"""Decide whether two query results hold the same data.

The two results come from different database engines, so equal data can
arrive looking different: 5 vs Decimal("5.000"), a date vs a midnight
timestamp, rows in another order. `normalize_value` removes those
representation differences; what is left is compared exactly.

Matching results are evidence that a translation is right, not proof: the
check sees only the rows this data set happens to produce.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal

from warehouse_scout.warehouse import QueryResult

Status = Literal["match", "mismatch", "source_error", "target_error", "not_comparable"]

# Division gives 38+ digits on Oracle and fewer on PostgreSQL. Six decimal
# places is far below any difference that matters in this data.
_DECIMAL_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class Comparison:
    status: Status
    detail: str
    source_rows: int | None = None
    target_rows: int | None = None


def normalize_value(value: Any) -> Any:
    """Bring a value to one canonical form, whichever engine produced it."""
    # bool is a subclass of int in Python, so it must be tested first.
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value)).quantize(_DECIMAL_PLACES, rounding=ROUND_HALF_EVEN)
    # datetime is a subclass of date, so it must be tested first as well.
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return value


def normalize_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Normalize every value and put the rows in a fixed order.

    Sorting makes the comparison ignore row order. The sort key turns each
    value into (is it None?, its text) so None and mixed types can be ordered.
    """
    normalized = [tuple(normalize_value(value) for value in row) for row in rows]
    return sorted(normalized, key=lambda row: [(value is None, str(value)) for value in row])


def compare_results(source: QueryResult, target: QueryResult) -> Comparison:
    if not source.ok:
        return Comparison("source_error", f"{source.error_code}: {source.error}")
    if not target.ok:
        return Comparison("target_error", f"{target.error_code}: {target.error}")

    counts = {"source_rows": len(source.rows), "target_rows": len(target.rows)}
    if source.truncated or target.truncated:
        return Comparison("not_comparable", "result exceeded the row limit", **counts)
    if len(source.columns) != len(target.columns):
        detail = f"{len(source.columns)} columns on source, {len(target.columns)} on target"
        return Comparison("mismatch", detail, **counts)
    if len(source.rows) != len(target.rows):
        detail = f"{len(source.rows)} rows on source, {len(target.rows)} on target"
        return Comparison("mismatch", detail, **counts)

    source_rows, target_rows = normalize_rows(source.rows), normalize_rows(target.rows)
    if source_rows == target_rows:
        return Comparison("match", "same rows", **counts)

    differing = sum(a != b for a, b in zip(source_rows, target_rows, strict=True))
    return Comparison("mismatch", f"{differing} of {len(source_rows)} rows differ", **counts)
