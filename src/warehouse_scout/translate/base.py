"""The contract every SQL translator fulfils.

`Translator` is a Protocol: Python's structural interface. A class satisfies
it by having a matching `name` and `translate`, without inheriting from it or
declaring anything (Java needs `implements`; Python checks the shape). That
keeps backends independent: sqlglot today, the BigQuery Translation API or an
LLM later, and the evals can run all of them through the same code.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Translation:
    """Outcome of translating one SQL text.

    Exactly one of `sql` and `error` is set. `warnings` lists constructs the
    translator knows it could not carry over; an empty tuple does not prove
    the output is correct, only that the translator noticed nothing.
    """

    sql: str | None
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Translator(Protocol):
    name: str

    def translate(self, sql: str, source: str, target: str) -> Translation:
        """Translate `sql` from the `source` dialect to the `target` dialect.

        Must not raise for bad input: a statement that cannot be translated is
        an expected outcome and is reported through `Translation.error`.
        """
        ...
