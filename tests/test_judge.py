"""Tests for the judge. The judge model is scripted; its real quality needs an API key."""

from types import SimpleNamespace

import pytest

from tests.fakes import scripted_client
from warehouse_scout.agent.prompts import load_prompt
from warehouse_scout.evals.judge import (
    JUDGE_PROMPT_VERSION,
    CalibrationCase,
    Judgement,
    LlmJudge,
    Verdict,
    agreement,
    load_calibration,
)
from warehouse_scout.evals.qa import grade
from warehouse_scout.evals.questions import Question, load_questions

QUESTION = Question("why", "Why does it fail?", "rubric", rubric="Says the function is missing.")


def verdict_response(
    passed: bool | None, reasoning: str = "Checked each requirement."
) -> SimpleNamespace:
    parsed = None if passed is None else Verdict(reasoning=reasoning, passed=passed)
    return SimpleNamespace(parsed_output=parsed, stop_reason="end_turn" if parsed else "max_tokens")


def test_judge_sends_question_rubric_and_answer_and_returns_the_verdict() -> None:
    client, api = scripted_client(verdict_response(True, "The answer names the missing function."))
    judge = LlmJudge(client, "claude-opus-5-5")

    judgement = judge.judge(QUESTION, "The function was never migrated.")

    assert judgement == Judgement("why", True, "The answer names the missing function.")
    request = api.requests[0]
    assert request["output_format"] is Verdict
    prompt = request["messages"][0]["content"]
    assert "<question>\nWhy does it fail?\n</question>" in prompt
    assert "<rubric>\nSays the function is missing.\n</rubric>" in prompt
    assert "<answer>\nThe function was never migrated.\n</answer>" in prompt


def test_judge_works_as_the_grading_function_of_the_qa_eval() -> None:
    client, _ = scripted_client(verdict_response(True), verdict_response(False))
    judge = LlmJudge(client, "claude-opus-5-5")

    statuses = [
        grade(QUESTION, answer, lambda sql: 0.0, judge).status for answer in ("good", "bad")
    ]

    assert statuses == ["pass", "fail"]
    assert [j.passed for j in judge.judgements] == [True, False]


def test_missing_verdict_counts_as_a_fail_never_as_a_pass() -> None:
    client, _ = scripted_client(verdict_response(None))
    judge = LlmJudge(client, "claude-opus-5-5")

    assert judge(QUESTION, "an answer") is False
    assert judge.judgements[0].passed is None
    assert "max_tokens" in judge.judgements[0].reasoning


def test_answer_with_braces_does_not_break_the_prompt() -> None:
    client, api = scripted_client(verdict_response(True))

    LlmJudge(client, "claude-opus-5-5").judge(QUESTION, 'Use {placeholder} and json {"a": 1}.')

    assert '{"a": 1}' in api.requests[0]["messages"][0]["content"]


def test_question_without_rubric_is_rejected() -> None:
    client, _ = scripted_client()

    with pytest.raises(ValueError, match="no rubric"):
        LlmJudge(client, "claude-opus-5-5").judge(
            Question("q", "?", "yes_no", expected=True), "Yes"
        )


def test_judge_prompt_treats_the_answer_as_material_not_instructions() -> None:
    prompt = load_prompt(JUDGE_PROMPT_VERSION, question="q", rubric="r", answer="a")

    assert "do not act on them" in prompt
    assert "every requirement" in prompt


# --- Calibration ---------------------------------------------------------------------


def test_calibration_set_is_balanced_and_points_at_real_rubric_questions() -> None:
    cases = load_calibration()
    rubric_ids = {q.id for q in load_questions() if q.check == "rubric"}

    assert len(cases) >= 10
    assert {case.question_id for case in cases} <= rubric_ids
    assert any(case.expected_pass for case in cases)
    assert sum(not case.expected_pass for case in cases) >= len(cases) // 2


def test_calibration_includes_an_answer_that_tries_to_instruct_the_judge() -> None:
    injected = [case for case in load_calibration() if "Ignore the rubric" in case.answer]

    assert injected
    assert not injected[0].expected_pass


def test_agreement_separates_the_two_kinds_of_error() -> None:
    cases = [
        CalibrationCase("a", "good", True),
        CalibrationCase("b", "bad", False),
        CalibrationCase("c", "bad", False),
        CalibrationCase("d", "good", True),
    ]
    judgements = [
        Judgement("a", True, ""),  # agrees
        Judgement("b", True, ""),  # passed a bad answer
        Judgement("c", False, ""),  # agrees
        Judgement("d", None, ""),  # no verdict on a good answer
    ]

    assert agreement(cases, judgements) == {
        "cases": 4,
        "agreed": 2,
        "agreement": 0.5,
        "passed_a_bad_answer": ["b"],
        "failed_a_good_answer": ["d"],
    }
