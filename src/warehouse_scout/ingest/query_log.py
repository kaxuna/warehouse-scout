"""Generate a synthetic query history for the source warehouse.

Output is a JSON Lines file (one JSON object per line), the shape a query-log
export has: when, who, the SQL text, how long it took.

    make querylog                      # 30 days, written to data/query_log.jsonl
    make querylog ARGS="--days 90"

The same seed always produces the same file, so eval results are comparable
between runs. The log carries no template ids: a real log would not, and the
agent has to work out which queries are "the same" by itself. The ids live in
the answer key (`evals/golden/query_templates.json`) instead.
"""

import argparse
import json
import random
import sys
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from string import Formatter

from warehouse_scout.ingest.query_templates import (
    PARAMETER_CHOICES,
    QUERY_TEMPLATES,
    QueryTemplate,
)

DEFAULT_DAYS = 30
DEFAULT_SEED = 20261114
DEFAULT_OUTPUT = Path("data/query_log.jsonl")
TEMPLATES_PATH = Path("evals/golden/query_templates.json")

# The log ends at a fixed moment, not "now", so it is identical on every machine.
LOG_END = datetime(2026, 11, 1)

# People work office hours; services run around the clock.
_OFFICE_HOURS = (8, 18)
_SERVICE_ACCOUNTS = frozenset({"bi_dashboard", "etl_svc", "monitoring", "finance_app"})


@dataclass(frozen=True)
class LogEntry:
    started_at: str  # ISO 8601
    user: str
    sql: str
    elapsed_ms: int


def placeholders(sql: str) -> set[str]:
    """Names of the {placeholders} in a template's SQL."""
    return {name for _, name, _, _ in Formatter().parse(sql) if name}


def render(template: QueryTemplate, rng: random.Random) -> str:
    """Fill a template's placeholders with randomly chosen literals."""
    values = {
        name: rng.choice(PARAMETER_CHOICES[name]) for name in sorted(placeholders(template.sql))
    }
    return template.sql.format(**values)


def runs_on_day(runs_per_day: float, rng: random.Random) -> int:
    """How often a template runs on one day.

    2.3 runs per day means two runs every day and a third on 30% of days.
    """
    whole = int(runs_per_day)
    fraction = runs_per_day - whole
    return whole + (rng.random() < fraction)


def _start_time(day: datetime, user: str, rng: random.Random) -> datetime:
    first_hour, last_hour = (0, 24) if user in _SERVICE_ACCOUNTS else _OFFICE_HOURS
    seconds_into_window = rng.randrange((last_hour - first_hour) * 3600)
    return day + timedelta(hours=first_hour, seconds=seconds_into_window)


def _elapsed_ms(typical_ms: int, rng: random.Random) -> int:
    # Log-normal: most runs land near the typical time, a few take far longer.
    # That long right tail is what real query durations look like.
    return max(1, round(typical_ms * rng.lognormvariate(0, 0.4)))


def generate_log(
    templates: Sequence[QueryTemplate] = QUERY_TEMPLATES,
    days: int = DEFAULT_DAYS,
    seed: int = DEFAULT_SEED,
) -> list[LogEntry]:
    """Build the full log, ordered by start time."""
    rng = random.Random(seed)
    first_day = LOG_END - timedelta(days=days)

    entries = [
        LogEntry(
            started_at=_start_time(day, template.user, rng).isoformat(),
            user=template.user,
            sql=render(template, rng),
            elapsed_ms=_elapsed_ms(template.typical_ms, rng),
        )
        for day in (first_day + timedelta(days=offset) for offset in range(days))
        for template in templates
        for _ in range(runs_on_day(template.runs_per_day, rng))
    ]
    return sorted(entries, key=lambda entry: entry.started_at)


def write_jsonl(entries: Sequence[LogEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as file:
        for entry in entries:
            file.write(json.dumps(asdict(entry)) + "\n")


def read_jsonl(path: Path) -> Iterator[LogEntry]:
    with path.open() as file:
        for line in file:
            yield LogEntry(**json.loads(line))


def build_answer_key(
    templates: Sequence[QueryTemplate] = QUERY_TEMPLATES,
) -> list[dict[str, object]]:
    """What the evals know about each template and the agent does not."""
    return [
        {
            "id": template.id,
            "user": template.user,
            "runs_per_day": template.runs_per_day,
            "features": sorted(template.features),
            "waste": template.waste,
            "duplicate_group": template.duplicate_group,
            "sql": template.sql,
        }
        for template in templates
    ]


def write_answer_key(path: Path = TEMPLATES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_answer_key(), indent=2) + "\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a synthetic Oracle query log.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--write-answer-key",
        action="store_true",
        help=f"also rewrite {TEMPLATES_PATH} from the templates",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    entries = generate_log(days=args.days, seed=args.seed)
    write_jsonl(entries, args.output)
    distinct = len({entry.sql for entry in entries})
    print(
        f"Wrote {len(entries):,} log entries ({distinct:,} distinct statements, "
        f"{len(QUERY_TEMPLATES)} templates) to {args.output}"
    )

    if args.write_answer_key:
        write_answer_key()
        print(f"Wrote {TEMPLATES_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
