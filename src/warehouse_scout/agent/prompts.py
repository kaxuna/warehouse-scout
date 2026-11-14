"""Prompts live in versioned files, not in code.

A prompt is the part of an LLM system that changes most often and matters
most for its behaviour. Keeping each version as its own file means an eval
result can name exactly which prompt produced it, and a diff between two
versions is readable.
"""

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
AGENT_PROMPT_VERSION = "agent_v1"


def load_prompt(version: str = AGENT_PROMPT_VERSION, **values: str) -> str:
    """Read a prompt file and fill its {placeholders}."""
    template = (PROMPTS_DIR / f"{version}.md").read_text()
    return template.format(**values).strip()
