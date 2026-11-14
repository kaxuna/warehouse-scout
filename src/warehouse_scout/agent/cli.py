"""Ask the agent one question from the command line.

    make ask Q="Translate to PostgreSQL: SELECT NVL(i_brand, 'none') FROM item WHERE ROWNUM <= 5"

Prints each tool call as it happened, then the answer, then what the run cost.
"""

import argparse
import json
import sys
from collections.abc import Sequence

import anthropic

from warehouse_scout.agent.loop import DEFAULT_MAX_STEPS, AgentResult, run_agent
from warehouse_scout.agent.prompts import AGENT_PROMPT_VERSION, load_prompt
from warehouse_scout.config import get_settings
from warehouse_scout.llm.client import estimate_cost_usd, has_credentials, make_client
from warehouse_scout.rag.embedder import make_embedder
from warehouse_scout.tools import build_tools
from warehouse_scout.translate import SqlglotTranslator


def format_trace(result: AgentResult) -> str:
    """One line per tool call: step, tool, arguments (shortened), outcome."""
    lines = []
    for call in result.tool_calls:
        arguments = json.dumps(call.arguments)
        if len(arguments) > 110:
            arguments = arguments[:107] + "..."
        outcome = "ERROR " + call.output[:80] if call.is_error else "ok"
        lines.append(f"[{call.step}] {call.name} {arguments} -> {outcome}")
    return "\n".join(lines)


def format_stats(result: AgentResult) -> str:
    cost = estimate_cost_usd(result.model, result.input_tokens, result.output_tokens)
    cost_text = "unknown" if cost is None else f"${cost:.4f}"
    return (
        f"{result.model} | prompt {AGENT_PROMPT_VERSION} | stop: {result.stop_reason} | "
        f"{result.steps} steps, {len(result.tool_calls)} tool calls | "
        f"{result.input_tokens} in, {result.output_tokens} out | cost {cost_text}"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask Warehouse Scout a question.")
    parser.add_argument("question", nargs="+", help="the question, quoted or as separate words")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    client = make_client(settings)
    if not has_credentials(client):
        print(
            "No API key found. Add ANTHROPIC_API_KEY to .env (see .env.example).", file=sys.stderr
        )
        return 1

    try:
        result = run_agent(
            client,
            model=settings.scout_model,
            system=load_prompt(source_schema=settings.oracle_user.upper()),
            tools=build_tools(
                settings, SqlglotTranslator(), make_embedder(settings.scout_embedder)
            ),
            question=" ".join(args.question),
            effort=settings.scout_effort,
            max_steps=args.max_steps,
        )
    except anthropic.APIError as error:
        print(f"API error: {error}", file=sys.stderr)
        return 1

    if result.tool_calls:
        print(format_trace(result))
        print()
    print(result.answer)
    print()
    print(format_stats(result))
    return 0 if result.stop_reason == "end_turn" else 1


if __name__ == "__main__":
    sys.exit(main())
