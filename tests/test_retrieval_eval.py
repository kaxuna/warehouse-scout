"""Tests for the retrieval eval's scoring and for the golden set as data."""

from warehouse_scout.evals.retrieval import (
    RetrievalCase,
    first_correct_rank,
    load_cases,
    score,
)
from warehouse_scout.ingest.noise_catalog import NOISE_FUNCTIONS, NOISE_TABLES, NOISE_VIEWS
from warehouse_scout.ingest.query_templates import QUERY_TEMPLATES
from warehouse_scout.ingest.tpcds_docs import TPCDS_TABLE_COMMENTS
from warehouse_scout.rag.chunks import fingerprint
from warehouse_scout.rag.search import KINDS, SearchHit


def hit(name: str, content: str = "") -> SearchHit:
    return SearchHit(kind="table", object_name=name, content=content, metadata={}, score=0.5)


def test_rank_is_the_position_of_the_first_right_answer() -> None:
    case = RetrievalCase("q", "table", expected_names=("ITEM", "DIM_ITEM"))

    assert first_correct_rank(case, [hit("ITEM")]) == 1
    assert first_correct_rank(case, [hit("STORE"), hit("FOO"), hit("DIM_ITEM"), hit("ITEM")]) == 3
    assert first_correct_rank(case, [hit("STORE")]) is None
    assert first_correct_rank(case, []) is None


def test_query_cases_match_on_a_fragment_of_the_sql() -> None:
    case = RetrievalCase("q", "query", expected_text=("MINUS SELECT",))

    assert first_correct_rank(case, [hit("Q001", "SELECT a"), hit("Q002", "x MINUS SELECT y")]) == 2


def test_score() -> None:
    # Ranks of the first right answer for four questions: 1st, 2nd, 5th, not found.
    assert score([1, 2, 5, None]) == {
        "recall_at_1": 0.25,
        "recall_at_5": 0.75,
        "mrr": round((1 + 1 / 2 + 1 / 5 + 0) / 4, 4),
    }
    assert score([]) == {"recall_at_1": 0.0, "recall_at_5": 0.0, "mrr": 0.0}


# --- The golden set must stay answerable ------------------------------------------


def test_golden_set_covers_every_kind() -> None:
    cases = load_cases()

    assert len(cases) >= 30
    assert {case.kind for case in cases} == set(KINDS)
    assert all(case.expected_names or case.expected_text for case in cases)


def test_every_expected_name_exists_in_the_warehouse() -> None:
    known = (
        {name.upper() for name in TPCDS_TABLE_COMMENTS}
        | {t.name.upper() for t in NOISE_TABLES}
        | {v.name.upper() for v in NOISE_VIEWS}
        | {f.name.upper() for f in NOISE_FUNCTIONS}
    )
    expected = {name for case in load_cases() for name in case.expected_names}

    assert expected - known == set()


def test_every_expected_sql_fragment_occurs_in_a_query_shape() -> None:
    shapes = [
        fingerprint(template.sql.replace("{", "").replace("}", "")) for template in QUERY_TEMPLATES
    ]
    fragments = [fragment for case in load_cases() for fragment in case.expected_text]

    assert [f for f in fragments if not any(f in shape for shape in shapes)] == []
