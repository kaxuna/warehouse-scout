"""Turn the stored snapshot into chunks: the units that get embedded and searched.

A chunk is a short text plus metadata. The text is what similarity search
sees, so it is written the way someone would describe the thing. The metadata
is what filters and ranking use, and what an answer can cite.

Four kinds, one chunk each:
    table, view   name, comment, column names (view: also its query)
    function      name and source code
    query         one per query *shape*: literals removed, so the 9,360 runs
                  of a polling query are one chunk with runs=9360
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from warehouse_scout.extract.oracle import SchemaObject

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_NUMBER_LITERAL = re.compile(r"\b\d+(?:\.\d+)?\b")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Chunk:
    kind: str  # "table", "view", "function" or "query"
    object_name: str  # the object's name; for a query, a short id
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryShape:
    """All log entries that are the same query apart from literal values."""

    fingerprint: str
    runs: int
    users: tuple[str, ...]
    average_ms: int


def fingerprint(sql: str) -> str:
    """Replace literals with "?" and collapse whitespace.

    `WHERE d_year = 2001` and `WHERE d_year = 1999` become the same text, so
    they count as one query shape.
    """
    without_strings = _STRING_LITERAL.sub("?", sql)
    without_numbers = _NUMBER_LITERAL.sub("?", without_strings)
    return _WHITESPACE.sub(" ", without_numbers).strip()


def query_shapes(log_rows: Iterable[tuple[str, str, int]]) -> list[QueryShape]:
    """Group (sql, user, elapsed_ms) log rows into shapes, most frequent first."""
    grouped: dict[str, list[tuple[str, int]]] = {}
    for sql, user, elapsed_ms in log_rows:
        grouped.setdefault(fingerprint(sql), []).append((user, elapsed_ms))

    shapes = [
        QueryShape(
            fingerprint=text,
            runs=len(runs),
            users=tuple(sorted({user for user, _ in runs})),
            average_ms=round(sum(elapsed for _, elapsed in runs) / len(runs)),
        )
        for text, runs in grouped.items()
    ]
    return sorted(shapes, key=lambda shape: (-shape.runs, shape.fingerprint))


def count_references(name: str, shapes: Sequence[QueryShape]) -> int:
    """How many logged runs mention `name` as a whole word."""
    pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    return sum(shape.runs for shape in shapes if pattern.search(shape.fingerprint))


def object_chunk(obj: SchemaObject, shapes: Sequence[QueryShape]) -> Chunk:
    kind = obj.object_type.lower()
    parts = [f"{kind.capitalize()} {obj.name}."]
    if obj.comment:
        parts.append(obj.comment)
    if obj.columns:
        parts.append("Columns: " + ", ".join(column.name.lower() for column in obj.columns) + ".")
    if obj.definition:
        parts.append(f"Definition: {obj.definition}")

    metadata: dict[str, Any] = {"query_runs": count_references(obj.name, shapes)}
    if obj.row_count is not None:
        metadata["row_count"] = obj.row_count
    return Chunk(kind=kind, object_name=obj.name, content=" ".join(parts), metadata=metadata)


def query_chunk(position: int, shape: QueryShape) -> Chunk:
    return Chunk(
        kind="query",
        object_name=f"Q{position:03d}",
        content=shape.fingerprint,
        metadata={"runs": shape.runs, "users": list(shape.users), "average_ms": shape.average_ms},
    )


def build_chunks(objects: Sequence[SchemaObject], shapes: Sequence[QueryShape]) -> list[Chunk]:
    return [
        *(object_chunk(obj, shapes) for obj in objects),
        *(query_chunk(position, shape) for position, shape in enumerate(shapes, start=1)),
    ]
