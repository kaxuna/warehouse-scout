"""The noise catalog is data, so these tests check its integrity, not behaviour."""

import json
import re
from collections import Counter

from warehouse_scout.ingest.noise_catalog import (
    NOISE_FUNCTIONS,
    NOISE_TABLES,
    NOISE_VIEWS,
    build_manifest,
)
from warehouse_scout.ingest.noise_tables import MANIFEST_PATH, comment_ddl, function_ddl
from warehouse_scout.ingest.tpcds_docs import TPCDS_TABLE_COMMENTS

IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")


def all_names() -> list[str]:
    return [
        *(table.name for table in NOISE_TABLES),
        *(view.name for view in NOISE_VIEWS),
        *(function.name for function in NOISE_FUNCTIONS),
    ]


def test_catalog_is_large_enough_to_make_search_hard() -> None:
    assert len(NOISE_TABLES) >= 100
    assert len(TPCDS_TABLE_COMMENTS) == 24


def test_names_are_plain_identifiers() -> None:
    assert [name for name in all_names() if not IDENTIFIER.fullmatch(name)] == []


def test_names_are_unique_and_do_not_shadow_benchmark_tables() -> None:
    counts = Counter([*all_names(), *TPCDS_TABLE_COMMENTS])
    assert [name for name, count in counts.items() if count > 1] == []


def test_every_duplicate_points_at_a_benchmark_table() -> None:
    targets = {table.duplicate_of for table in NOISE_TABLES if table.duplicate_of}
    assert targets <= set(TPCDS_TABLE_COMMENTS)


def test_every_duplicate_function_group_has_at_least_two_members() -> None:
    groups = build_manifest()["duplicate_function_groups"]
    assert isinstance(groups, dict)
    assert groups
    assert all(len(names) >= 2 for names in groups.values())


def test_both_statuses_are_present_in_every_object_type() -> None:
    for objects in (NOISE_TABLES, NOISE_VIEWS, NOISE_FUNCTIONS):
        assert {obj.status for obj in objects} == {"active", "dead"}


def test_committed_manifest_matches_the_catalog() -> None:
    # If this fails, the catalog changed: run `make noise ARGS=--write-manifest`.
    assert json.loads(MANIFEST_PATH.read_text()) == build_manifest()


def test_function_ddl() -> None:
    quarter = next(f for f in NOISE_FUNCTIONS if f.name == "get_qtr")
    assert function_ddl(quarter) == (
        "CREATE OR REPLACE FUNCTION get_qtr (d DATE) RETURN NUMBER DETERMINISTIC IS "
        "BEGIN RETURN CEIL(EXTRACT(MONTH FROM d) / 3); END;"
    )


def test_comment_ddl_escapes_single_quotes() -> None:
    assert comment_ddl("dim_item", "BI team's copy") == (
        "COMMENT ON TABLE dim_item IS 'BI team''s copy'"
    )
