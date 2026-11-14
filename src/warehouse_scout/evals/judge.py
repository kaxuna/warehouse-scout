"""A judge model for answers that cannot be checked by rule.

Some questions ask for an explanation or advice. Whether such an answer is
good depends on what it says, not on one name or number in it, so a second
model grades it against a written rubric.

A judge is itself a model that can be wrong. Three things keep it honest:
    a rubric per question   it grades against stated requirements, not taste
    a versioned prompt      a change of judge behaviour is a visible diff
    a calibration set       answers a person has already labelled pass/fail;
                            `make eval-judge` measures how often the judge agrees

    make eval-judge          # needs an API key
"""

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from pydantic import BaseModel

from warehouse_scout.agent.prompts import load_prompt
from warehouse_scout.config import get_settings
from warehouse_scout.evals.questions import Question, load_questions
from warehouse_scout.llm.client import has_credentials, make_client

JUDGE_PROMPT_VERSION = "judge_v1"
CALIBRATION_PATH = Path("evals/golden/judge_calibration.jsonl")
MAX_TOKENS = 4_000


class Verdict(BaseModel):
    """What the judge must return. The API guarantees this shape.

    `reasoning` comes before `passed` on purpose: the model writes fields in
    order, so it reasons first and decides second.
    """

    reasoning: str
    passed: bool


@dataclass(frozen=True)
class Judgement:
    question_id: str
    passed: bool | None  # None: the judge returned no verdict
    reasoning: str


@dataclass
class LlmJudge:
    client: anthropic.Anthropic
    model: str
    prompt_version: str = JUDGE_PROMPT_VERSION
    judgements: list[Judgement] = field(default_factory=list)

    def judge(self, question: Question, answer: str) -> Judgement:
        if not question.rubric:
            raise ValueError(f"Question {question.id!r} has no rubric to judge against")
        prompt = load_prompt(
            self.prompt_version, question=question.question, rubric=question.rubric, answer=answer
        )
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            output_format=Verdict,
        )
        verdict = response.parsed_output
        judgement = (
            Judgement(question.id, None, f"no verdict (stop reason: {response.stop_reason})")
            if verdict is None
            else Judgement(question.id, verdict.passed, verdict.reasoning)
        )
        self.judgements.append(judgement)
        return judgement

    def __call__(self, question: Question, answer: str) -> bool:
        """Makes the judge usable wherever a `Judge` function is expected.
        No verdict counts as a fail: an unjudged answer must not pass."""
        return self.judge(question, answer).passed is True


@dataclass(frozen=True)
class CalibrationCase:
    question_id: str
    answer: str
    expected_pass: bool


def load_calibration(path: Path = CALIBRATION_PATH) -> list[CalibrationCase]:
    return [CalibrationCase(**json.loads(line)) for line in path.read_text().splitlines()]


def agreement(
    cases: Sequence[CalibrationCase], judgements: Sequence[Judgement]
) -> dict[str, object]:
    """How often the judge's verdict equals the human label."""
    pairs = list(zip(cases, judgements, strict=True))
    agreed = sum(case.expected_pass == judgement.passed for case, judgement in pairs)
    return {
        "cases": len(pairs),
        "agreed": agreed,
        "agreement": round(agreed / len(pairs), 4) if pairs else 0.0,
        # The two ways to be wrong are not equally bad: passing a bad answer
        # inflates the agent's score; failing a good one only deflates it.
        "passed_a_bad_answer": [
            case.question_id for case, j in pairs if not case.expected_pass and j.passed is True
        ],
        "failed_a_good_answer": [
            case.question_id for case, j in pairs if case.expected_pass and j.passed is not True
        ],
    }


def main() -> int:
    settings = get_settings()
    client = make_client(settings)
    if not has_credentials(client):
        print("The judge needs ANTHROPIC_API_KEY in .env (see .env.example).", file=sys.stderr)
        return 1

    questions = {question.id: question for question in load_questions()}
    cases = load_calibration()
    judge = LlmJudge(client, settings.scout_judge_model)
    judgements = [judge.judge(questions[case.question_id], case.answer) for case in cases]
    result = agreement(cases, judgements)

    for case, judgement in zip(cases, judgements, strict=True):
        if case.expected_pass != judgement.passed:
            print(f"DISAGREE {case.question_id}: judge said {judgement.passed}")
            print(f"  {judgement.reasoning[:200]}")
    print(
        f"\n{judge.model}, prompt {judge.prompt_version}: agrees with the human label on "
        f"{result['agreed']} of {result['cases']} cases ({result['agreement']:.0%})."
    )
    print(f"passed a bad answer: {result['passed_a_bad_answer']}")
    print(f"failed a good answer: {result['failed_a_good_answer']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
