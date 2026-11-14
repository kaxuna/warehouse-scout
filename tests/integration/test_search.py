"""Search against the stored index. Needs `make extract` and `make index` (both embedders)."""

import json
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_postgres
from warehouse_scout.evals.retrieval import evaluate, load_cases, score
from warehouse_scout.rag.embedder import Embedder, FastEmbedEmbedder, HashEmbedder
from warehouse_scout.rag.search import search
from warehouse_scout.tools import build_tools, execute_tool
from warehouse_scout.translate import SqlglotTranslator

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def neural() -> Embedder:
    return FastEmbedEmbedder()


@pytest.fixture
def pg() -> Iterator[psycopg.Connection[Any]]:
    with connect_postgres(get_settings()) as connection:
        yield connection


def test_search_finds_by_meaning_not_by_shared_words(
    pg: psycopg.Connection[Any], neural: Embedder
) -> None:
    hits = search(pg, neural, "who reports to whom in the company", kinds=["table"])

    assert hits[0].object_name == "EMPLOYEE"
    assert hits[0].score > hits[-1].score
    assert hits[0].metadata["query_runs"] > 0


def test_kind_filter_restricts_results(pg: psycopg.Connection[Any], neural: Embedder) -> None:
    hits = search(pg, neural, "quarter of a date", kinds=["function"], limit=3)

    assert {hit.kind for hit in hits} == {"function"}
    assert {"FISCAL_QUARTER", "GET_QTR"} & {hit.object_name for hit in hits}


def test_embedders_do_not_see_each_others_vectors(pg: psycopg.Connection[Any]) -> None:
    unknown = HashEmbedder()
    unknown.name = "never-indexed"

    assert search(pg, unknown, "anything") == []


def test_neural_embedder_beats_word_counting_on_the_golden_set(
    pg: psycopg.Connection[Any], neural: Embedder
) -> None:
    cases = load_cases()

    neural_score = score(evaluate(pg, neural, cases, filter_by_kind=True))
    word_score = score(evaluate(pg, HashEmbedder(), cases, filter_by_kind=True))

    # Loose bounds: a guard against a broken index, not a pin on exact numbers.
    assert neural_score["recall_at_5"] >= 0.8
    assert neural_score["recall_at_5"] > word_score["recall_at_5"] + 0.3


def test_search_schema_tool_returns_usage_figures(neural: Embedder) -> None:
    tools = {t.name: t for t in build_tools(get_settings(), SqlglotTranslator(), neural)}

    outcome = execute_tool(
        tools, "search_schema", {"query": "products and their prices", "kind": "table"}
    )

    hits = json.loads(outcome.content)
    assert not outcome.is_error
    assert any(hit["name"] in {"ITEM", "DIM_ITEM"} for hit in hits)
    assert {"kind", "name", "score", "text", "query_runs", "row_count"} <= set(hits[0])


def test_search_tool_is_absent_without_an_embedder() -> None:
    names = {t.name for t in build_tools(get_settings(), SqlglotTranslator())}

    assert "search_schema" not in names
