"""Rule-based translation with sqlglot: parse into a syntax tree, print in another dialect.

Fast, free and deterministic. It is also the baseline the agent has to beat:
sqlglot rewrites what it has rules for (NVL, DECODE, MINUS, ...) and passes
the rest through unchanged, often without a warning. See the tests for the
known gaps.
"""

import sqlglot
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import ErrorLevel, SqlglotError

from warehouse_scout.translate.base import Translation


class SqlglotTranslator:
    name = "sqlglot"

    def translate(self, sql: str, source: str, target: str) -> Translation:
        try:
            expressions = sqlglot.parse(sql, read=source)
            # IGNORE: do not raise on a construct the target lacks; collect a
            # message instead, so the caller gets both output and warnings.
            generator = Dialect.get_or_raise(target).generator(unsupported_level=ErrorLevel.IGNORE)
        except (SqlglotError, ValueError) as error:
            # ValueError is what sqlglot raises for an unknown dialect name.
            return Translation(sql=None, error=f"{type(error).__name__}: {error}")

        statements: list[str] = []
        warnings: list[str] = []
        for expression in expressions:
            if expression is None:  # an empty statement, e.g. a trailing ";"
                continue
            try:
                statements.append(generator.generate(expression))
            except SqlglotError as error:
                return Translation(sql=None, error=f"{type(error).__name__}: {error}")
            warnings.extend(generator.unsupported_messages)

        if not statements:
            return Translation(sql=None, error="No SQL statement found")

        # dict.fromkeys removes repeated warnings and keeps their first-seen order.
        return Translation(sql=";\n".join(statements), warnings=tuple(dict.fromkeys(warnings)))
