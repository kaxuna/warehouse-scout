"""Duplicate detection against the real source, scored with the answer key.

Needs `make load`, `make noise` and `make extract` first.
"""

import json

import pytest

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_postgres
from warehouse_scout.duplicates.functions import find_duplicate_functions, load_signatures
from warehouse_scout.duplicates.tables import find_duplicate_tables, rows_equal_on_source
from warehouse_scout.evals.duplicates import expected_pairs
from warehouse_scout.ingest.noise_tables import MANIFEST_PATH
from warehouse_scout.rag.index import load_schema

pytestmark = pytest.mark.integration


def test_every_function_has_a_signature() -> None:
    signatures = {s.name: s for s in load_signatures(get_settings())}

    assert len(signatures) == 12
    assert signatures["GET_QTR"].shape == ("DATE", "->", "NUMBER")
    assert signatures["FN_NET_PRICE"].shape == ("NUMBER", "NUMBER", "->", "NUMBER")


def test_duplicate_functions_match_the_answer_key_exactly() -> None:
    expected = expected_pairs(json.loads(MANIFEST_PATH.read_text()))["function"]

    assert find_duplicate_functions(get_settings()) == expected


def test_duplicate_tables_match_the_answer_key_exactly() -> None:
    settings = get_settings()
    expected = expected_pairs(json.loads(MANIFEST_PATH.read_text()))["table"]
    with connect_postgres(settings) as pg:
        objects = load_schema(pg)

    assert find_duplicate_tables(objects, rows_equal_on_source(settings)) == expected


def test_same_shape_but_different_rows_is_not_a_duplicate() -> None:
    rows_equal = rows_equal_on_source(get_settings())

    assert rows_equal("ITEM", "DIM_ITEM")
    assert not rows_equal("STORE_SALES", "OLD_STORE_SALES_2019")  # a 2,000-row slice
