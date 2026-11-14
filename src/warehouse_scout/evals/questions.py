"""The golden question set, built from the project's ground truth.

Every expected answer is derived from something already known for certain:
the noise catalog (what is dead, what duplicates what), the query templates
(who runs what), or a query run at eval time (row counts). Nothing is typed
in by hand as a fact, so the questions cannot drift from the data.

Check kinds:
    yes_no     the answer must say yes, or no
    names_all  every listed name must be mentioned
    names_any  at least one listed name must be mentioned
    number     a number in the answer must equal the expected one
    rubric     free text, scored by the judge model against the rubric

`build_questions()` is the source; `evals/golden/questions.jsonl` is its
committed output, kept in sync by a test.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from warehouse_scout.ingest.noise_catalog import (
    NOISE_FUNCTIONS,
    NOISE_TABLES,
    NOISE_VIEWS,
    build_manifest,
)
from warehouse_scout.ingest.query_templates import QUERY_TEMPLATES

QUESTIONS_PATH = Path("evals/golden/questions.jsonl")

CHECK_KINDS = ("yes_no", "names_all", "names_any", "number", "rubric")


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    check: str
    expected: Any = None  # bool, list of names, number, or None when expected_sql is used
    expected_sql: str | None = None  # number questions: run on the source to get the answer
    rubric: str | None = None


def _usage_questions() -> list[Question]:
    """Is this table used? Five that are, five that are not."""
    by_status = {
        status: [t.name.upper() for t in NOISE_TABLES if t.status == status]
        for status in ("active", "dead")
    }
    # Every third name, so the sample spans the kinds of junk rather than one prefix.
    picks = [(name, True) for name in by_status["active"][::4][:5]]
    picks += [(name, False) for name in by_status["dead"][::17][:5]]
    return [
        Question(
            id=f"used_{name.lower()}",
            question=f"Is the table {name} referenced by any query in the query log? "
            "Answer yes or no first.",
            check="yes_no",
            expected=used,
        )
        for name, used in picks
    ]


def _duplicate_questions() -> list[Question]:
    copies = [("ITEM2", "ITEM"), ("CUSTOMER_COPY", "CUSTOMER"), ("DIM_STORE", "STORE"),
              ("PROMOTION_BAK_20230630", "PROMOTION")]  # fmt: skip
    tables = [
        Question(
            id=f"copy_of_{copy.lower()}",
            question=f"The table {copy} holds the same rows as a benchmark table. Which one?",
            check="names_all",
            expected=[original],
        )
        for copy, original in copies
    ]

    groups: dict[str, list[str]] = {}
    for function in NOISE_FUNCTIONS:
        if function.group:
            groups.setdefault(function.group, []).append(function.name.upper())
    functions = [
        Question(
            id=f"same_as_{names[0].lower()}",
            question=f"Name another function in the source schema that returns the same "
            f"result as {names[0]} for every input.",
            check="names_any",
            expected=names[1:],
        )
        for names in groups.values()
    ]
    return tables + functions


def _count_questions() -> list[Question]:
    manifest = build_manifest()
    dead_tables = manifest["dead_tables"]
    assert isinstance(dead_tables, list)
    fixed = [
        ("count_dead_tables", "How many tables in the source schema are never referenced "
         "by the query log?", len(dead_tables)),
        ("count_functions", "How many functions does the source schema contain?",
         len(NOISE_FUNCTIONS)),
        ("count_views", "How many views does the source schema contain?", len(NOISE_VIEWS)),
        ("count_query_shapes", "How many distinct query shapes does the query log contain, "
         "counting queries that differ only in literal values as one?", len(QUERY_TEMPLATES)),
    ]  # fmt: skip
    from_data = [
        Question(
            id=f"rows_{table.lower()}",
            question=f"How many rows does the table {table} hold in the source?",
            check="number",
            expected_sql=f"SELECT COUNT(*) FROM {table}",
        )
        for table in ("STORE_SALES", "CUSTOMER", "DATE_DIM", "EMPLOYEE")
    ]
    return [Question(id, text, "number", expected=count) for id, text, count in fixed] + from_data


def _workload_questions() -> list[Question]:
    specs: list[tuple[str, str, str, list[str]]] = [
        ("top_user", "Which database user runs the most queries in the query log?",
         "names_all", ["monitoring"]),
        ("top_table", "Which table is referenced by the most query runs in the log?",
         "names_all", ["STORE_SALES"]),
        ("dashboard_view", "Which view does the query that selects sales_date, store_sk and "
         "revenue read from?", "names_all", ["V_DAILY_STORE_SALES"]),
        ("org_chart_table", "Which table does the hierarchical query with "
         "SYS_CONNECT_BY_PATH read?", "names_all", ["EMPLOYEE"]),
        ("staging_of_web_returns", "Which staging table is loaded from WEB_RETURNS?",
         "names_all", ["STG_WEB_RETURNS"]),
        ("view_function", "Which user-defined function does the view V_ITEM_MARGIN call?",
         "names_all", ["MARGIN_PCT"]),
        ("merge_targets", "Which two tables are maintained with MERGE statements?",
         "names_all", ["DIM_CUSTOMER", "DIM_ITEM"]),
        ("bi_customer_copy", "Analysts query a copy of CUSTOMER instead of CUSTOMER itself. "
         "Which table is that copy?", "names_all", ["DIM_CUSTOMER"]),
    ]  # fmt: skip
    return [Question(id, text, check, expected=names) for id, text, check, names in specs]


def _migration_questions() -> list[Question]:
    yes_no: list[tuple[str, str, bool]] = [
        ("log_has_connect_by", "Does the query log contain queries that use CONNECT BY?", True),
        ("log_has_outer_plus", "Does the query log contain queries that use Oracle's (+) "
         "outer join syntax?", True),
        ("log_has_pivot", "Does the query log contain a query that uses PIVOT?", True),
        ("log_has_model", "Does the query log contain a query that uses the MODEL clause?",
         False),
        ("log_has_flashback", "Does the query log contain a flashback query "
         "(AS OF TIMESTAMP)?", False),
    ]  # fmt: skip
    rewrites: list[tuple[str, str, list[str]]] = [
        ("replace_nvl", "Which standard SQL function replaces Oracle's NVL in PostgreSQL?",
         ["COALESCE"]),
        ("replace_minus", "Which set operator replaces Oracle's MINUS in PostgreSQL?",
         ["EXCEPT"]),
        ("replace_listagg", "Which PostgreSQL aggregate replaces Oracle's LISTAGG?",
         ["STRING_AGG"]),
        ("replace_decode", "Which standard SQL expression replaces Oracle's DECODE?",
         ["CASE"]),
        ("replace_sysdate", "Name a PostgreSQL expression that replaces Oracle's SYSDATE.",
         ["CURRENT_TIMESTAMP", "NOW", "LOCALTIMESTAMP", "CURRENT_DATE"]),
    ]  # fmt: skip
    return [
        *(Question(id, f"{text} Answer yes or no first.", "yes_no", expected=answer)
          for id, text, answer in yes_no),
        *(Question(id, text, "names_any", expected=names) for id, text, names in rewrites),
    ]  # fmt: skip


def _rubric_questions() -> list[Question]:
    specs = [
        (
            "explain_missing_function",
            "A query calls fiscal_quarter(d_date). Why does its direct translation fail on the "
            "target, and what are the options?",
            "Says the function exists only on the source and was not migrated. Gives at least "
            "one workable option: migrate the function, or inline its logic (quarter of the "
            "date) into the query.",
        ),
        (
            "explain_outer_join_risk",
            "What is the risk of translating Oracle's (+) join syntax with a rule-based "
            "translator, and how would you catch it?",
            "Says (+) can be dropped or turned into an inner join, which silently removes "
            "unmatched rows without any error. Says to rewrite as LEFT JOIN and to verify by "
            "comparing results on both databases.",
        ),
        (
            "wasteful_patterns",
            "Which wasteful query patterns appear in the query log? Name each and the query "
            "that shows it.",
            "Identifies all three: a SELECT * over store_sales with no filter; a join of "
            "customer and customer_demographics with no join condition (cartesian product); "
            "and the same COUNT/SUM over store_sales run hundreds of times a day.",
        ),
        (
            "duplicate_report",
            "Two queries in the log produce the same report with different SQL. Which are "
            "they and how do they differ?",
            "Identifies the two revenue-by-category queries. Notes that one uses comma joins "
            "on item and date_dim and the other ANSI joins on dim_item and dim_date, which "
            "are copies of those tables.",
        ),
        (
            "what_not_to_migrate",
            "Which kinds of tables in the source should not be migrated, and on what evidence?",
            "Names backups, abandoned copies, deprecated versions and scratch tables. Bases "
            "the judgement on evidence: no references in the query log, and/or identical "
            "content to another table. Does not recommend dropping staging tables or the "
            "copies analysts actively query.",
        ),
        (
            "dim_customer_advice",
            "What is DIM_CUSTOMER, and should it be migrated?",
            "Says it is a copy of CUSTOMER kept by another team and refreshed by a MERGE "
            "statement, and that queries in the log use it. Concludes it is in use, so it "
            "must either be migrated or its users moved to CUSTOMER; does not call it dead.",
        ),
        (
            "connect_by_rewrite",
            "How should the org-chart query using CONNECT BY PRIOR be rewritten for PostgreSQL?",
            "Says to use a recursive common table expression (WITH RECURSIVE). Mentions that "
            "LEVEL and SYS_CONNECT_BY_PATH have to be computed in the recursive part.",
        ),
        (
            "rownum_rewrite",
            "How should `SELECT * FROM (... ORDER BY revenue DESC) WHERE ROWNUM <= 10` be "
            "rewritten for PostgreSQL?",
            "Says to replace the ROWNUM filter with LIMIT 10 (or FETCH FIRST 10 ROWS ONLY) "
            "after the ORDER BY. Keeps the ordering so the same top rows are returned.",
        ),
    ]
    return [Question(id, text, "rubric", rubric=rubric) for id, text, rubric in specs]


def build_questions() -> list[Question]:
    return [
        *_usage_questions(),
        *_duplicate_questions(),
        *_count_questions(),
        *_workload_questions(),
        *_migration_questions(),
        *_rubric_questions(),
    ]


def write_questions(path: Path = QUESTIONS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(asdict(q)) + "\n" for q in build_questions()))


def load_questions(path: Path = QUESTIONS_PATH) -> list[Question]:
    return [Question(**json.loads(line)) for line in path.read_text().splitlines()]


if __name__ == "__main__":
    write_questions()
    print(f"Wrote {len(build_questions())} questions to {QUESTIONS_PATH}")
