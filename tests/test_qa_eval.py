"""Tests for the answer checks, the golden questions as data, and grading."""

import json

import pytest

from tests.fakes import response, scripted_client, text_block
from warehouse_scout.evals import checks
from warehouse_scout.evals.qa import AgentAnswerer, Graded, grade, run_eval, summarize
from warehouse_scout.evals.questions import (
    CHECK_KINDS,
    QUESTIONS_PATH,
    Question,
    build_questions,
    load_questions,
)
from warehouse_scout.ingest.noise_catalog import NOISE_FUNCTIONS, NOISE_TABLES, NOISE_VIEWS
from warehouse_scout.ingest.tpcds_docs import TPCDS_TABLE_COMMENTS

ROW_COUNT = 1234.0


def lookup(sql: str) -> float:
    return ROW_COUNT


# --- Checks -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "name", "expected"),
    [
        ("It is a copy of ITEM.", "ITEM", True),
        ("it is a copy of `item`", "ITEM", True),
        ("ITEM2 is a copy of DIM_ITEM", "ITEM", False),  # only longer names appear
        ("Use COALESCE(a, 0)", "COALESCE", True),
        ("the monitoring user", "monitoring", True),
    ],
)
def test_mentions_matches_whole_names_only(answer: str, name: str, expected: bool) -> None:
    assert checks.mentions(answer, name) is expected


def test_names_all_and_names_any() -> None:
    answer = "DIM_CUSTOMER and DIM_ITEM are merged nightly."

    assert checks.names_all(answer, ["DIM_CUSTOMER", "DIM_ITEM"])
    assert not checks.names_all(answer, ["DIM_CUSTOMER", "DIM_STORE"])
    assert checks.names_any(answer, ["DIM_STORE", "DIM_ITEM"])
    assert not checks.names_any(answer, ["DIM_STORE"])


@pytest.mark.parametrize(
    ("answer", "expected", "found"),
    [
        ("There are 288,464 rows.", 288464, True),
        ("About 81 of the 126 tables are unused.", 81, True),
        ("About 81 of the 126 tables are unused.", 80, False),
        ("The total is 12.0", 12, True),
        ("None found.", 0, False),
    ],
)
def test_has_number(answer: str, expected: float, found: bool) -> None:
    assert checks.has_number(answer, expected) is found


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Yes. It appears in three queries.", True),
        ("No, nothing references it.", False),
        ("**No** — the log has no such query.", False),
        ("It is unclear.", None),
        ("Nothing uses it.", None),  # "Nothing" is not "no"
    ],
)
def test_yes_or_no_reads_the_first_verdict(answer: str, expected: bool | None) -> None:
    assert checks.yes_or_no(answer) is expected


# --- The golden set as data --------------------------------------------------------


def test_golden_set_has_about_fifty_questions_of_every_kind() -> None:
    questions = build_questions()

    assert len(questions) >= 50
    assert {q.check for q in questions} == set(CHECK_KINDS)
    assert len({q.id for q in questions}) == len(questions)


def test_committed_file_matches_the_generator() -> None:
    # If this fails, run: uv run python -m warehouse_scout.evals.questions
    assert load_questions() == build_questions()
    assert all(json.loads(line) for line in QUESTIONS_PATH.read_text().splitlines())


def test_every_question_can_be_checked() -> None:
    for q in build_questions():
        if q.check == "rubric":
            assert q.rubric, q.id
        elif q.check == "number":
            assert (q.expected is None) != (q.expected_sql is None), q.id
        else:
            assert q.expected is not None, q.id


def test_expected_object_names_exist() -> None:
    known = (
        {name.upper() for name in TPCDS_TABLE_COMMENTS}
        | {t.name.upper() for t in NOISE_TABLES}
        | {v.name.upper() for v in NOISE_VIEWS}
        | {f.name.upper() for f in NOISE_FUNCTIONS}
    )
    named = {
        name
        for q in build_questions()
        # "replace_*" questions expect SQL keywords, "top_user" a user name: not objects.
        if q.check in ("names_all", "names_any") and not q.id.startswith(("replace_", "top_user"))
        for name in q.expected
    }

    assert named - known == set()


def perfect_answer(question: Question) -> str:
    match question.check:
        case "yes_no":
            return "Yes." if question.expected else "No."
        case "number":
            value = ROW_COUNT if question.expected_sql else question.expected
            return f"The answer is {value:,.0f}."
        case _:
            return "It is " + " and ".join(question.expected) + "."


def test_a_correct_answer_passes_and_a_wrong_one_fails_for_every_fact_question() -> None:
    for question in build_questions():
        if question.check == "rubric":
            continue
        wrong = "Yes." if question.expected is False else "No, the answer is -1 and it is NOTHING."

        assert grade(question, perfect_answer(question), lookup).status == "pass", question.id
        assert grade(question, wrong, lookup).status == "fail", question.id


# --- Grading -----------------------------------------------------------------------


def test_rubric_questions_wait_for_a_judge() -> None:
    question = Question("why", "Why?", "rubric", rubric="Says because.")

    assert grade(question, "Because.", lookup).status == "needs_judge"
    assert grade(question, "Because.", lookup, judge=lambda q, a: True).status == "pass"
    assert grade(question, "Dunno.", lookup, judge=lambda q, a: False).status == "fail"


def test_unknown_check_kind_is_an_error() -> None:
    with pytest.raises(ValueError, match="telepathy"):
        grade(Question("x", "?", "telepathy"), "answer", lookup)


def test_summary_scores_only_checked_answers() -> None:
    graded = [
        Graded("a", "yes_no", "pass", ""),
        Graded("b", "yes_no", "fail", ""),
        Graded("c", "number", "pass", ""),
        Graded("d", "rubric", "needs_judge", ""),
    ]

    assert summarize(graded) == {
        "total": 4,
        "scored": 3,
        "passed": 2,
        "accuracy": round(2 / 3, 4),
        "by_check": {"number": "1/1", "yes_no": "1/2"},
        "needs_judge": 1,
    }


def test_agent_answerer_runs_the_agent_and_reports_usage() -> None:
    client, _ = scripted_client(
        response(text_block("Yes. Three queries read it."), input_tokens=1000, output_tokens=50),
        response(text_block("No."), input_tokens=500, output_tokens=10),
    )
    answerer = AgentAnswerer(client=client, model="claude-opus-5-5", system="s", tools=[])
    questions = [
        Question("q1", "Is A used?", "yes_no", expected=True),
        Question("q2", "Is B used?", "yes_no", expected=True),
    ]

    graded = run_eval(answerer, questions, lookup)

    assert [g.status for g in graded] == ["pass", "fail"]
    usage = answerer.usage_summary()
    assert (usage["input_tokens"], usage["output_tokens"]) == (1500, 60)
    assert usage["cost_per_question_usd"] == pytest.approx((1500 * 4 + 60 * 20) / 1e6 / 2, abs=1e-4)
