"""Question-answering eval: ask the golden questions, check the answers.

    make eval-qa ARGS="--limit 5"    # smoke run, not recorded (costs money)
    make eval-qa                     # all questions

The system under test is anything with `answer(question) -> str`. Answers to
fact questions are checked deterministically (`checks.py`). Rubric questions
are handed to a judge when one is supplied; without one they are reported as
"needs_judge" and left out of the score.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import anthropic

from warehouse_scout.agent.loop import AgentResult, Effort, run_agent
from warehouse_scout.agent.prompts import AGENT_PROMPT_VERSION, load_prompt
from warehouse_scout.config import Settings, get_settings
from warehouse_scout.evals import checks
from warehouse_scout.evals.questions import Question, load_questions
from warehouse_scout.evals.translation import append_history, current_commit
from warehouse_scout.llm.client import estimate_cost_usd, has_credentials, make_client
from warehouse_scout.rag.embedder import make_embedder
from warehouse_scout.tools import Tool, build_tools
from warehouse_scout.translate import SqlglotTranslator
from warehouse_scout.warehouse import run_readonly

HISTORY_PATH = Path("evals/history/qa.jsonl")

# (question, answer) -> did the answer satisfy the rubric?
Judge = Callable[[Question, str], bool]
# SQL -> the single number it returns on the source
NumberLookup = Callable[[str], float]


class Answerer(Protocol):
    name: str

    def answer(self, question: str) -> str: ...


@dataclass(frozen=True)
class Graded:
    question_id: str
    check: str
    status: str  # "pass", "fail" or "needs_judge"
    answer: str


def lookup_on_source(settings: Settings) -> NumberLookup:
    def lookup(sql: str) -> float:
        result = run_readonly(settings, "source", sql)
        if not result.ok:
            raise RuntimeError(f"Could not compute the expected answer: {result.error}")
        return float(result.rows[0][0])

    return lookup


def grade(
    question: Question, answer: str, lookup: NumberLookup, judge: Judge | None = None
) -> Graded:
    """Decide whether one answer is right."""
    passed: bool | None
    match question.check:
        case "yes_no":
            passed = checks.is_yes_no(answer, question.expected)
        case "names_all":
            passed = checks.names_all(answer, question.expected)
        case "names_any":
            passed = checks.names_any(answer, question.expected)
        case "number":
            expected = lookup(question.expected_sql) if question.expected_sql else question.expected
            passed = checks.has_number(answer, float(expected))
        case "rubric":
            passed = judge(question, answer) if judge else None
        case _:
            raise ValueError(f"Unknown check kind {question.check!r}")

    status = "needs_judge" if passed is None else ("pass" if passed else "fail")
    return Graded(question.id, question.check, status, answer)


def summarize(graded: Sequence[Graded]) -> dict[str, object]:
    scored = [g for g in graded if g.status != "needs_judge"]
    passed = sum(g.status == "pass" for g in scored)
    by_check = {
        check: f"{sum(g.status == 'pass' for g in group)}/{len(group)}"
        for check in sorted({g.check for g in scored})
        if (group := [g for g in scored if g.check == check])
    }
    return {
        "total": len(graded),
        "scored": len(scored),
        "passed": passed,
        "accuracy": round(passed / len(scored), 4) if scored else 0.0,
        "by_check": by_check,
        "needs_judge": len(graded) - len(scored),
    }


@dataclass
class AgentAnswerer:
    client: anthropic.Anthropic
    model: str
    system: str
    tools: Sequence[Tool]
    effort: Effort = "medium"
    name: str = "agent"
    runs: list[AgentResult] = field(default_factory=list)

    def answer(self, question: str) -> str:
        try:
            result = run_agent(
                self.client,
                model=self.model,
                system=self.system,
                tools=self.tools,
                question=question,
                effort=self.effort,
            )
        except anthropic.AnthropicError as error:
            return f"[no answer: {type(error).__name__}: {error}]"
        self.runs.append(result)
        return result.answer

    def usage_summary(self) -> dict[str, object]:
        input_tokens = sum(run.input_tokens for run in self.runs)
        output_tokens = sum(run.output_tokens for run in self.runs)
        cost = estimate_cost_usd(self.model, input_tokens, output_tokens)
        per_question = None if cost is None or not self.runs else round(cost / len(self.runs), 4)
        return {
            "model": self.model,
            "prompt_version": AGENT_PROMPT_VERSION,
            "effort": self.effort,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": None if cost is None else round(cost, 4),
            "cost_per_question_usd": per_question,
        }


def run_eval(
    answerer: Answerer,
    questions: Sequence[Question],
    lookup: NumberLookup,
    judge: Judge | None = None,
) -> list[Graded]:
    return [grade(q, answerer.answer(q.question), lookup, judge) for q in questions]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the question-answering eval on the agent.")
    parser.add_argument("--limit", type=int, help="ask only the first N questions")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    client = make_client(settings)
    if not has_credentials(client):
        print("The agent needs ANTHROPIC_API_KEY in .env (see .env.example).", file=sys.stderr)
        return 1

    answerer = AgentAnswerer(
        client=client,
        model=settings.scout_model,
        system=load_prompt(source_schema=settings.oracle_user.upper()),
        tools=build_tools(settings, SqlglotTranslator(), make_embedder(settings.scout_embedder)),
        effort=settings.scout_effort,
    )
    questions = load_questions()[: args.limit]
    graded = run_eval(answerer, questions, lookup_on_source(settings))
    summary = summarize(graded)

    for item in graded:
        if item.status == "fail":
            print(f"FAIL {item.question_id}: {item.answer[:160]!r}")
    print(
        f"\n{answerer.name}: {summary['passed']} of {summary['scored']} checked answers pass "
        f"({summary['accuracy']:.0%}); {summary['needs_judge']} need the judge."
    )
    print(f"by check: {summary['by_check']}")
    print(f"statuses: {dict(Counter(g.status for g in graded))}")
    usage = answerer.usage_summary()
    print(f"usage: {usage}")

    if args.limit is None:
        append_history(
            {"system": answerer.name, "commit": current_commit(), **summary, **usage},
            HISTORY_PATH,
        )
        print(f"Recorded in {HISTORY_PATH}")
    else:
        print("Partial run (--limit): not recorded in the history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
