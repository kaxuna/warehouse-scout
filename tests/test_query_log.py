"""Tests for the query templates and the log generator. No database needed."""

import json
import random
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from warehouse_scout.ingest.noise_catalog import NOISE_FUNCTIONS, NOISE_TABLES, NOISE_VIEWS
from warehouse_scout.ingest.query_log import (
    LOG_END,
    TEMPLATES_PATH,
    build_answer_key,
    generate_log,
    placeholders,
    read_jsonl,
    render,
    runs_on_day,
    write_jsonl,
)
from warehouse_scout.ingest.query_templates import PARAMETER_CHOICES, QUERY_TEMPLATES
from warehouse_scout.ingest.tpcds_docs import TPCDS_TABLE_COMMENTS

ANALYSTS = {"jsmith", "mlee", "apatel", "kwong"}

# Everything the workload can reach: the templates, plus the views they select
# from (a function called only inside a live view is still in use).
WORKLOAD_SQL = "\n".join(
    [t.sql for t in QUERY_TEMPLATES] + [v.select_sql for v in NOISE_VIEWS if v.status == "active"]
)


def mentions(name: str, sql: str = WORKLOAD_SQL) -> bool:
    r"""True if `name` appears as a whole word. `\b` treats `_` as part of a word,
    so `item` does not match inside `dim_item` or `ss_item_sk`."""
    return re.search(rf"\b{re.escape(name)}\b", sql, re.IGNORECASE) is not None


def calls(name: str, sql: str = WORKLOAD_SQL) -> bool:
    """True if `name` is called as a function: followed by an opening parenthesis."""
    return re.search(rf"\b{re.escape(name)}\s*\(", sql, re.IGNORECASE) is not None


# --- Templates as data ---------------------------------------------------------


def test_template_ids_are_unique() -> None:
    counts = Counter(template.id for template in QUERY_TEMPLATES)
    assert [template_id for template_id, count in counts.items() if count > 1] == []


def test_every_placeholder_has_choices() -> None:
    used = set().union(*(placeholders(template.sql) for template in QUERY_TEMPLATES))
    assert used <= set(PARAMETER_CHOICES)


def test_planted_defects_are_present() -> None:
    assert {t.waste for t in QUERY_TEMPLATES if t.waste} == {
        "select_star_without_filter",
        "cartesian_join",
        "excessive_frequency",
    }
    groups = Counter(t.duplicate_group for t in QUERY_TEMPLATES if t.duplicate_group)
    assert groups
    assert all(count >= 2 for count in groups.values())


def test_committed_answer_key_matches_the_templates() -> None:
    # If this fails, a template changed: run `make querylog ARGS=--write-answer-key`.
    assert json.loads(TEMPLATES_PATH.read_text()) == build_answer_key()


# --- The promise made by the noise catalog -------------------------------------


def test_workload_uses_every_benchmark_table() -> None:
    assert [table for table in TPCDS_TABLE_COMMENTS if not mentions(table)] == []


def test_workload_uses_every_active_object() -> None:
    unused_tables = [t.name for t in NOISE_TABLES if t.status == "active" and not mentions(t.name)]
    unused_views = [v.name for v in NOISE_VIEWS if v.status == "active" and not mentions(v.name)]
    unused_functions = [
        f.name for f in NOISE_FUNCTIONS if f.status == "active" and not calls(f.name)
    ]
    assert (unused_tables, unused_views, unused_functions) == ([], [], [])


def test_workload_never_touches_a_dead_object() -> None:
    touched_tables = [t.name for t in NOISE_TABLES if t.status == "dead" and mentions(t.name)]
    touched_views = [v.name for v in NOISE_VIEWS if v.status == "dead" and mentions(v.name)]
    touched_functions = [f.name for f in NOISE_FUNCTIONS if f.status == "dead" and calls(f.name)]
    assert (touched_tables, touched_views, touched_functions) == ([], [], [])


# --- Rendering -----------------------------------------------------------------


def test_render_fills_every_placeholder() -> None:
    rng = random.Random(7)
    for template in QUERY_TEMPLATES:
        sql = render(template, rng)
        assert "{" not in sql, template.id
        assert "}" not in sql, template.id


@pytest.mark.parametrize(
    ("runs_per_day", "allowed"),
    [(0.3, {0, 1}), (1, {1}), (2.5, {2, 3}), (24, {24})],
)
def test_runs_on_day_stays_next_to_the_rate(runs_per_day: float, allowed: set[int]) -> None:
    rng = random.Random(3)
    assert {runs_on_day(runs_per_day, rng) for _ in range(200)} == allowed


# --- The log -------------------------------------------------------------------


def test_same_seed_gives_the_same_log() -> None:
    assert generate_log(days=3, seed=11) == generate_log(days=3, seed=11)
    assert generate_log(days=3, seed=11) != generate_log(days=3, seed=12)


def test_log_is_ordered_and_inside_its_time_window() -> None:
    entries = generate_log(days=5)
    starts = [datetime.fromisoformat(entry.started_at) for entry in entries]

    assert starts == sorted(starts)
    assert (LOG_END - starts[0]).days <= 5
    assert starts[-1] < LOG_END


def test_analysts_work_office_hours() -> None:
    hours = {
        datetime.fromisoformat(entry.started_at).hour
        for entry in generate_log(days=10)
        if entry.user in ANALYSTS
    }
    assert hours <= set(range(8, 18))


def test_frequent_templates_dominate_the_log() -> None:
    by_sql = Counter(entry.sql for entry in generate_log(days=10))
    most_common_sql, runs = by_sql.most_common(1)[0]

    assert most_common_sql == "SELECT COUNT(*), SUM(ss_net_paid) FROM store_sales"
    assert runs == 288 * 10


def test_jsonl_round_trip(tmp_path: Path) -> None:
    entries = generate_log(days=1)
    path = tmp_path / "nested" / "log.jsonl"

    write_jsonl(entries, path)

    assert list(read_jsonl(path)) == entries
