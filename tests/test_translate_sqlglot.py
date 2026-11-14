"""What the sqlglot backend does, and, in the second half, what it gets wrong."""

import pytest

from warehouse_scout.translate import SqlglotTranslator, Translation, Translator


def to_postgres(sql: str) -> Translation:
    return SqlglotTranslator().translate(sql, source="oracle", target="postgres")


def test_backend_satisfies_the_translator_protocol() -> None:
    # No assertion needed at run time: this line is checked by mypy. If
    # SqlglotTranslator stopped matching the Protocol, `make typecheck` fails.
    translator: Translator = SqlglotTranslator()
    assert translator.name == "sqlglot"


# --- Rewrites it knows -----------------------------------------------------------


@pytest.mark.parametrize(
    ("oracle_sql", "postgres_sql"),
    [
        ("SELECT NVL(a, 0) FROM t", "SELECT COALESCE(a, 0) FROM t"),
        ("SELECT a FROM t MINUS SELECT a FROM u", "SELECT a FROM t EXCEPT SELECT a FROM u"),
        (
            "SELECT DECODE(a, 'Y', 1, 0) FROM t",
            "SELECT CASE WHEN a = 'Y' THEN 1 ELSE 0 END FROM t",
        ),
        (
            "SELECT LISTAGG(a, ', ') WITHIN GROUP (ORDER BY a) FROM t",
            "SELECT STRING_AGG(a, ', ' ORDER BY a) FROM t",
        ),
    ],
)
def test_known_rewrites(oracle_sql: str, postgres_sql: str) -> None:
    result = to_postgres(oracle_sql)

    assert result.ok
    assert result.sql == postgres_sql
    assert result.warnings == ()


def test_several_statements_are_translated_and_joined() -> None:
    result = to_postgres("SELECT NVL(a, 0) FROM t; SELECT 1 FROM dual;")

    assert result.sql is not None
    assert result.sql.count(";\n") == 1
    assert result.sql.startswith("SELECT COALESCE(a, 0) FROM t")


# --- Failures it reports ---------------------------------------------------------


def test_unparseable_sql_is_an_error_not_an_exception() -> None:
    result = to_postgres("SELECT FROM WHERE (")

    assert not result.ok
    assert result.sql is None
    assert result.error is not None
    assert result.error.startswith("ParseError")


def test_unknown_dialect_is_an_error_not_an_exception() -> None:
    result = SqlglotTranslator().translate("SELECT 1", source="oracle", target="klingon")

    assert not result.ok
    assert "klingon" in (result.error or "")


def test_empty_input_is_an_error() -> None:
    assert to_postgres("  ;  ").error == "No SQL statement found"


def test_repeated_warnings_are_reported_once() -> None:
    result = to_postgres("SELECT 1 FROM a, b, c WHERE a.id = b.id(+) AND a.id = c.id(+)")

    assert result.warnings == ("Outer join syntax using the (+) operator is not supported.",)


# --- Known gaps: the baseline the agent has to beat --------------------------------
#
# These tests pin down today's wrong behaviour on purpose. Each one is a case
# where the output is not valid PostgreSQL, or is valid but means something
# else. If a sqlglot upgrade fixes one, the test fails and the gap list shrinks.


def test_gap_outer_join_plus_silently_becomes_an_inner_join() -> None:
    result = to_postgres("SELECT c.id FROM c, a WHERE c.addr = a.id(+)")

    # A warning is raised, but the output still runs and returns fewer rows.
    assert result.sql == "SELECT c.id FROM c, a WHERE c.addr = a.id"
    assert result.warnings != ()


@pytest.mark.parametrize(
    ("oracle_sql", "left_untranslated"),
    [
        ("SELECT a FROM t WHERE ROWNUM <= 10", "ROWNUM"),
        ("SELECT LEVEL FROM dual CONNECT BY LEVEL <= 12", "CONNECT BY"),
        ("SELECT ADD_MONTHS(d, 1) FROM t", "ADD_MONTHS"),
        ("SELECT MAX(a) KEEP (DENSE_RANK FIRST ORDER BY b) FROM t", "KEEP"),
    ],
)
def test_gap_oracle_only_syntax_passes_through_without_warning(
    oracle_sql: str, left_untranslated: str
) -> None:
    result = to_postgres(oracle_sql)

    assert result.ok
    assert result.warnings == ()
    assert left_untranslated in (result.sql or "")


def test_gap_pivot_is_dropped_from_the_output() -> None:
    result = to_postgres(
        "SELECT * FROM (SELECT q, amount FROM t) PIVOT (SUM(amount) FOR q IN (1 AS q1, 2 AS q2))"
    )

    assert "PIVOT" not in (result.sql or "")
    assert result.warnings == ("PIVOT unsupported",)
