"""Duplicate functions: same inputs in, same outputs out.

Functions that "do the same thing" can look nothing alike: different names,
parameter names, and SQL (NVL vs COALESCE vs CASE). So the source text is not
compared at all. Each function is called with the same sample inputs and
functions whose outputs agree on every input are reported as duplicates.
This is differential testing.

It is evidence, not proof: two functions that differ only on an input the
samples do not contain would be reported as duplicates.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import product

from warehouse_scout.compare import normalize_value
from warehouse_scout.config import Settings
from warehouse_scout.duplicates.pairs import Pair, pairs_of
from warehouse_scout.sqlutil import checked_identifier
from warehouse_scout.warehouse import run_readonly

# Sample values per Oracle type, written as SQL literals. Chosen to hit the
# usual edges: NULL, zero, negative, fractions, blank and padded strings, and
# dates covering every weekday and every quarter.
SAMPLES: dict[str, tuple[str, ...]] = {
    "NUMBER": ("100", "15", "0", "-5", "0.5", "33.333", "1959", "1985", "CAST(NULL AS NUMBER)"),
    "VARCHAR2": ("'Ada'", "' Ada '", "'x  y'", "'O''Brien'", "CAST(NULL AS VARCHAR2(10))"),
    "DATE": (
        *(f"DATE '2024-01-{day:02d}'" for day in range(1, 8)),  # one full week
        *(f"DATE '2024-{month:02d}-15'" for month in range(1, 13)),  # every month
        "DATE '2023-12-31'",
        "CAST(NULL AS DATE)",
    ),
}

MAX_SAMPLE_ROWS = 2_000


@dataclass(frozen=True)
class FunctionSignature:
    name: str
    parameter_types: tuple[str, ...]
    return_type: str

    @property
    def shape(self) -> tuple[str, ...]:
        """What two functions must share to be comparable at all."""
        return (*self.parameter_types, "->", self.return_type)


def load_signatures(settings: Settings) -> list[FunctionSignature]:
    """Parameter and return types of every function, from Oracle's catalog."""
    result = run_readonly(
        settings,
        "source",
        # position 0 is the return value; parameters start at 1.
        "SELECT a.object_name, a.position, a.data_type FROM all_arguments a "
        "JOIN all_objects o ON o.owner = a.owner AND o.object_name = a.object_name "
        f"WHERE a.owner = '{checked_identifier(settings.oracle_user).upper()}' "
        "AND o.object_type = 'FUNCTION' ORDER BY a.object_name, a.position",
        max_rows=10_000,
    )
    if not result.ok:
        raise RuntimeError(f"Could not read function signatures: {result.error}")

    types: dict[str, list[str]] = {}
    for name, _position, data_type in result.rows:
        types.setdefault(name, []).append(data_type)
    return [FunctionSignature(name, tuple(t[1:]), t[0]) for name, t in types.items()]


def sample_query(functions: Sequence[FunctionSignature]) -> str:
    """One query that calls every function in `functions` on every sample input.

    All functions must share a shape. The inputs form a small inline table,
    one row per combination of sample values; each function is one column.
    """
    parameter_types = functions[0].parameter_types
    columns = [f"p{index}" for index in range(len(parameter_types))]
    combinations = product(*(SAMPLES[data_type] for data_type in parameter_types))
    input_rows = " UNION ALL ".join(
        "SELECT "
        + ", ".join(f"{value} AS {column}" for value, column in zip(row, columns, strict=True))
        + " FROM dual"
        for row in combinations
    )
    calls = ", ".join(
        f"{checked_identifier(function.name)}({', '.join(columns)})" for function in functions
    )
    return f"SELECT {calls} FROM ({input_rows})"


def group_by_behaviour(
    functions: Sequence[FunctionSignature], rows: Sequence[Sequence[object]]
) -> list[list[str]]:
    """Group functions whose output columns are equal on every row."""
    by_outputs: dict[tuple[object, ...], list[str]] = {}
    for index, function in enumerate(functions):
        outputs = tuple(normalize_value(row[index]) for row in rows)
        by_outputs.setdefault(outputs, []).append(function.name)
    return [names for names in by_outputs.values() if len(names) > 1]


def find_duplicate_functions(settings: Settings) -> set[Pair]:
    """Pairs of functions that return the same result for every sample input."""
    by_shape: dict[tuple[str, ...], list[FunctionSignature]] = {}
    for signature in load_signatures(settings):
        if all(data_type in SAMPLES for data_type in signature.parameter_types):
            by_shape.setdefault(signature.shape, []).append(signature)

    duplicates: set[Pair] = set()
    for functions in by_shape.values():
        if len(functions) < 2:
            continue
        result = run_readonly(settings, "source", sample_query(functions), max_rows=MAX_SAMPLE_ROWS)
        if not result.ok:
            raise RuntimeError(f"Sample query failed: {result.error_code}: {result.error}")
        duplicates |= pairs_of(group_by_behaviour(functions, result.rows))
    return duplicates
