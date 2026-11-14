"""Duplicate tables: same columns, same number of rows, same rows.

Three checks, cheapest first, each run only on what survived the one before:
    1. identical column names and types   from the stored snapshot, no query
    2. identical row count                from the stored snapshot, no query
    3. identical rows                     two MINUS queries on the source

No model is involved. "Do these two tables hold the same data" is a question
with an exact answer, so it gets an exact method.
"""

from collections.abc import Callable, Sequence

from warehouse_scout.config import Settings
from warehouse_scout.duplicates.pairs import Pair
from warehouse_scout.extract.oracle import SchemaObject
from warehouse_scout.sqlutil import checked_identifier
from warehouse_scout.warehouse import run_readonly

# (table_a, table_b) -> do they hold the same rows?
RowsEqual = Callable[[str, str], bool]

COMPARE_TIMEOUT_SECONDS = 120.0


def candidate_groups(objects: Sequence[SchemaObject]) -> list[list[SchemaObject]]:
    """Tables grouped by identical columns and row count; groups of one are dropped."""
    by_shape: dict[tuple[object, ...], list[SchemaObject]] = {}
    for obj in objects:
        if obj.object_type == "TABLE":
            shape = (obj.row_count, *((column.name, column.data_type) for column in obj.columns))
            by_shape.setdefault(shape, []).append(obj)
    return [group for group in by_shape.values() if len(group) > 1]


def rows_equal_on_source(settings: Settings) -> RowsEqual:
    """Build the check that compares two tables' rows on the source database."""

    def rows_equal(table_a: str, table_b: str) -> bool:
        a, b = checked_identifier(table_a), checked_identifier(table_b)
        for first, second in ((a, b), (b, a)):
            # MINUS returns the rows of the first table that the second lacks.
            result = run_readonly(
                settings,
                "source",
                f"SELECT COUNT(*) FROM (SELECT * FROM {first} MINUS SELECT * FROM {second})",
                timeout_seconds=COMPARE_TIMEOUT_SECONDS,
            )
            if not result.ok or result.rows[0][0] != 0:
                return False
        return True

    return rows_equal


def find_duplicate_tables(objects: Sequence[SchemaObject], rows_equal: RowsEqual) -> set[Pair]:
    """Pairs of tables that hold identical rows."""
    duplicates: set[Pair] = set()
    for group in candidate_groups(objects):
        # Each inner list is a set of tables already known to be identical.
        # A new table is compared with one member of each: "same rows" is
        # transitive, so one comparison per class is enough.
        classes: list[list[str]] = []
        for table in sorted(obj.name for obj in group):
            for members in classes:
                if rows_equal(members[0], table):
                    duplicates.update(frozenset((member, table)) for member in members)
                    members.append(table)
                    break
            else:  # no break: the table matched no existing class
                classes.append([table])
    return duplicates
