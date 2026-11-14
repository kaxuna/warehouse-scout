"""Tests for duplicate detection. Databases are replaced by small stand-ins."""

from decimal import Decimal

import pytest

from warehouse_scout.duplicates.functions import (
    SAMPLES,
    FunctionSignature,
    group_by_behaviour,
    sample_query,
)
from warehouse_scout.duplicates.pairs import groups_of, pairs_of, precision_recall
from warehouse_scout.duplicates.tables import candidate_groups, find_duplicate_tables
from warehouse_scout.evals.duplicates import best_threshold, expected_pairs
from warehouse_scout.extract.oracle import ColumnInfo, SchemaObject
from warehouse_scout.ingest.noise_catalog import duplicate_table_groups


def pair(a: str, b: str) -> frozenset[str]:
    return frozenset((a, b))


def table(name: str, rows: int = 10, columns: tuple[str, ...] = ("ID",)) -> SchemaObject:
    infos = tuple(ColumnInfo(column, "NUMBER", True) for column in columns)
    return SchemaObject(name, "TABLE", row_count=rows, columns=infos)


# --- Pairs and scoring ------------------------------------------------------------


def test_pairs_of_a_group_are_all_its_two_member_combinations() -> None:
    assert pairs_of([["A", "B", "C"], ["X", "Y"], ["ALONE"]]) == {
        pair("A", "B"),
        pair("A", "C"),
        pair("B", "C"),
        pair("X", "Y"),
    }


def test_groups_are_rebuilt_from_pairs_transitively() -> None:
    assert groups_of([pair("A", "B"), pair("C", "B"), pair("X", "Y")]) == [
        ["A", "B", "C"],
        ["X", "Y"],
    ]
    assert groups_of(pairs_of([["A", "B", "C", "D"]])) == [["A", "B", "C", "D"]]
    assert groups_of([]) == []


def test_precision_and_recall() -> None:
    expected = {pair("A", "B"), pair("C", "D"), pair("E", "F"), pair("G", "H")}
    predicted = {pair("A", "B"), pair("C", "D"), pair("X", "Y")}

    assert precision_recall(predicted, expected) == {
        "predicted": 3,
        "correct": 2,
        "precision": round(2 / 3, 4),  # 2 of the 3 predictions are right
        "recall": 0.5,  # 2 of the 4 real pairs were found
        "f1": round(2 * (2 / 3) * 0.5 / (2 / 3 + 0.5), 4),
    }


def test_precision_and_recall_with_nothing_predicted_or_expected() -> None:
    assert precision_recall(set(), {pair("A", "B")})["f1"] == 0.0
    assert precision_recall({pair("A", "B")}, set())["recall"] == 0.0


# --- Tables -----------------------------------------------------------------------


def test_candidates_share_columns_and_row_count() -> None:
    objects = [
        table("ITEM"),
        table("ITEM_BAK"),
        table("ITEM_SAMPLE", rows=5),  # same columns, fewer rows
        table("OTHER", columns=("ID", "NAME")),  # same rows, other columns
        SchemaObject("V_ITEM", "VIEW", columns=(ColumnInfo("ID", "NUMBER", True),)),
    ]

    groups = candidate_groups(objects)

    assert [[obj.name for obj in group] for group in groups] == [["ITEM", "ITEM_BAK"]]


def test_duplicates_need_equal_rows_and_one_comparison_per_class_is_enough() -> None:
    # A, B, C hold the same rows; D has the same shape but different rows.
    contents = {"A": 1, "B": 1, "C": 1, "D": 2}
    comparisons: list[tuple[str, str]] = []

    def rows_equal(first: str, second: str) -> bool:
        comparisons.append((first, second))
        return contents[first] == contents[second]

    duplicates = find_duplicate_tables([table(name) for name in "DCBA"], rows_equal)

    assert duplicates == pairs_of([["A", "B", "C"]])
    # B and C are each compared with A only; D with A, and nothing else exists yet.
    assert comparisons == [("A", "B"), ("A", "C"), ("A", "D")]


def test_two_separate_classes_inside_one_candidate_group() -> None:
    contents = {"A": 1, "B": 2, "C": 1, "D": 2}

    duplicates = find_duplicate_tables(
        [table(name) for name in "ABCD"], lambda first, second: contents[first] == contents[second]
    )

    assert duplicates == {pair("A", "C"), pair("B", "D")}


# --- Functions --------------------------------------------------------------------


def test_sample_query_calls_every_function_on_every_input_combination() -> None:
    functions = [
        FunctionSignature("F_ONE", ("NUMBER", "DATE"), "NUMBER"),
        FunctionSignature("F_TWO", ("NUMBER", "DATE"), "NUMBER"),
    ]

    sql = sample_query(functions)

    assert sql.startswith("SELECT F_ONE(p0, p1), F_TWO(p0, p1) FROM (")
    assert sql.count("FROM dual") == len(SAMPLES["NUMBER"]) * len(SAMPLES["DATE"])
    assert "CAST(NULL AS NUMBER) AS p0" in sql


def test_functions_are_grouped_by_identical_outputs() -> None:
    functions = [FunctionSignature(name, ("NUMBER",), "NUMBER") for name in ("A", "B", "C", "D")]
    rows = [
        # A and C agree everywhere (85 vs 85.0 is the same number);
        # B differs once; D differs on NULL handling.
        (85, 85, Decimal("85.0"), 85),
        (10, 11, 10, 10),
        (None, None, None, 0),
    ]

    assert group_by_behaviour(functions, rows) == [["A", "C"]]


def test_shape_separates_functions_that_cannot_be_compared() -> None:
    quarter = FunctionSignature("GET_QTR", ("DATE",), "NUMBER")
    net = FunctionSignature("FN_NET_PRICE", ("NUMBER", "NUMBER"), "NUMBER")

    assert quarter.shape == ("DATE", "->", "NUMBER")
    assert quarter.shape != net.shape


# --- Eval helpers and the answer key ----------------------------------------------


def test_best_threshold_maximises_f1() -> None:
    expected = {pair("A", "B"), pair("C", "D")}
    similarities = [
        (pair("A", "B"), 0.95),
        (pair("C", "D"), 0.80),
        (pair("A", "C"), 0.78),
        (pair("B", "D"), 0.40),
    ]

    threshold, scores = best_threshold(similarities, expected)

    assert threshold == pytest.approx(0.80)
    assert (scores["precision"], scores["recall"]) == (1.0, 1.0)


def test_answer_key_groups_copies_with_their_original_and_with_each_other() -> None:
    groups = duplicate_table_groups()

    assert [
        "DIM_ITEM",
        "ITEM",
        "ITEM2",
        "ITEM_BAK_20230131",
        "ITEM_BAK_20230630",
        "ITEM_BAK_20231231",
    ] in groups
    # The same scratch query run by four people gives four identical tables.
    assert [f"TMP_{user}_TOP_ITEMS" for user in ("APATEL", "JSMITH", "KWONG", "MLEE")] in groups
    assert all(len(group) > 1 for group in groups)


def test_expected_pairs_come_from_the_manifest() -> None:
    manifest = {
        "duplicate_table_groups": [["A", "B", "C"]],
        "duplicate_function_groups": {"net_price": ["F", "G"]},
    }

    expected = expected_pairs(manifest)

    assert len(expected["table"]) == 3
    assert expected["function"] == {pair("F", "G")}
