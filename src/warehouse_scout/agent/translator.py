"""The agent, wearing the `Translator` interface.

This is what lets the eval score "sqlglot alone" and "agent" with the same
cases and the same check: both are just something with `translate()`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import anthropic

from warehouse_scout.agent.loop import DEFAULT_MAX_STEPS, AgentResult, Effort, run_agent
from warehouse_scout.agent.prompts import AGENT_PROMPT_VERSION
from warehouse_scout.llm.client import estimate_cost_usd
from warehouse_scout.tools import Tool
from warehouse_scout.translate import Translation

TASK = """\
Translate this {source} query to {target} so that it returns the same rows.

```sql
{sql}
```"""

# A fenced code block, optionally tagged "sql". DOTALL lets "." span lines.
_SQL_BLOCK = re.compile(r"```(?:sql)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql(answer: str) -> str | None:
    """The SQL from the last code block of an answer, or None if there is none.

    The last block, because an answer may quote the original query first and
    give the translation after it.
    """
    blocks = _SQL_BLOCK.findall(answer)
    if not blocks:
        return None
    return blocks[-1].strip().rstrip(";").strip() or None


@dataclass
class AgentTranslator:
    client: anthropic.Anthropic
    model: str
    system: str
    tools: Sequence[Tool]
    effort: Effort = "medium"
    max_steps: int = DEFAULT_MAX_STEPS
    name: str = "agent"
    # Every run so far, kept so the eval can report tokens and cost.
    runs: list[AgentResult] = field(default_factory=list)

    def translate(self, sql: str, source: str, target: str) -> Translation:
        try:
            result = run_agent(
                self.client,
                model=self.model,
                system=self.system,
                tools=self.tools,
                question=TASK.format(source=source, target=target, sql=sql),
                effort=self.effort,
                max_steps=self.max_steps,
            )
        except anthropic.AnthropicError as error:
            # One failed API call must not end an eval of dozens of cases.
            return Translation(sql=None, error=f"{type(error).__name__}: {error}")

        self.runs.append(result)
        translated = extract_sql(result.answer)
        if translated is None:
            return Translation(
                sql=None, error=f"No SQL block in the answer (stop reason: {result.stop_reason})"
            )
        return Translation(sql=translated)

    def usage_summary(self) -> dict[str, object]:
        """Totals over all runs: what the eval records as the price of its score."""
        input_tokens = sum(run.input_tokens for run in self.runs)
        output_tokens = sum(run.output_tokens for run in self.runs)
        model = self.runs[-1].model if self.runs else self.model
        cost = estimate_cost_usd(model, input_tokens, output_tokens)
        return {
            "model": model,
            "prompt_version": AGENT_PROMPT_VERSION,
            "effort": self.effort,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_calls": sum(len(run.tool_calls) for run in self.runs),
            "cost_usd": None if cost is None else round(cost, 4),
            "cost_per_case_usd": (
                None if cost is None or not self.runs else round(cost / len(self.runs), 4)
            ),
        }
