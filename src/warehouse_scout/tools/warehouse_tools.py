"""The agent's three tools: query a database, translate SQL, compare two queries."""

import json
from typing import Any

from warehouse_scout.compare import compare_results
from warehouse_scout.config import Settings
from warehouse_scout.tools.base import Tool, ToolError
from warehouse_scout.translate import Translator
from warehouse_scout.warehouse import QueryResult, run_readonly

# Rows shown to the model per query. Results cost tokens on every later step
# of the conversation, so this is far below what the database could return.
MODEL_MAX_ROWS = 50

# Rows fetched per side when comparing. The comparison happens in Python and
# only its verdict goes to the model, so this limit can be generous.
COMPARE_MAX_ROWS = 100_000
COMPARE_TIMEOUT_SECONDS = 60.0

_DATABASE_PROPERTY = {
    "type": "string",
    "enum": ["source", "target"],
    "description": "source = the Oracle warehouse being migrated; target = PostgreSQL.",
}


def _object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    """A JSON Schema for an object where every property is required."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def format_result(result: QueryResult) -> str:
    """A query result as compact JSON for the model."""
    payload = {
        "columns": result.columns,
        "rows": result.rows,
        "row_count": len(result.rows),
        "truncated": result.truncated,
    }
    # default=str renders Decimal and datetime values, which JSON has no type for.
    return json.dumps(payload, default=str)


def build_tools(settings: Settings, translator: Translator) -> list[Tool]:
    """Create the tools, bound to this configuration and translator.

    The handlers are closures: functions defined here that keep using
    `settings` and `translator` after `build_tools` has returned.
    """

    def run_readonly_sql(arguments: dict[str, Any]) -> str:
        result = run_readonly(
            settings, arguments["database"], arguments["sql"], max_rows=MODEL_MAX_ROWS
        )
        if not result.ok:
            raise ToolError(f"{result.error_code}: {result.error}")
        return format_result(result)

    def translate_sql(arguments: dict[str, Any]) -> str:
        translation = translator.translate(
            arguments["sql"], arguments["source_dialect"], arguments["target_dialect"]
        )
        if translation.sql is None:
            raise ToolError(translation.error or "Translation produced no output.")
        return json.dumps(
            {
                "translator": translator.name,
                "sql": translation.sql,
                "warnings": translation.warnings,
                "verified": False,
            }
        )

    def compare_query_results(arguments: dict[str, Any]) -> str:
        source = run_readonly(
            settings,
            "source",
            arguments["source_sql"],
            max_rows=COMPARE_MAX_ROWS,
            timeout_seconds=COMPARE_TIMEOUT_SECONDS,
        )
        target = run_readonly(
            settings,
            "target",
            arguments["target_sql"],
            max_rows=COMPARE_MAX_ROWS,
            timeout_seconds=COMPARE_TIMEOUT_SECONDS,
        )
        comparison = compare_results(source, target)
        return json.dumps(
            {
                "status": comparison.status,
                "detail": comparison.detail,
                "source_rows": comparison.source_rows,
                "target_rows": comparison.target_rows,
            }
        )

    return [
        Tool(
            name="run_readonly_sql",
            description=(
                "Run one SQL statement and return its rows as JSON. Read-only: the "
                "database rejects anything that changes data. Use it to inspect schemas "
                "(Oracle: ALL_TABLES, ALL_TAB_COLUMNS, ALL_TAB_COMMENTS, ALL_VIEWS, "
                "ALL_SOURCE; PostgreSQL: information_schema) and to look at data. "
                f"At most {MODEL_MAX_ROWS} rows are returned; 'truncated' says if there "
                "were more, so aggregate or filter in SQL rather than reading tables "
                "whole. Statements are cancelled after 15 seconds. A database error is "
                "returned as the tool error, with the database's own error code."
            ),
            input_schema=_object_schema(
                {
                    "database": _DATABASE_PROPERTY,
                    "sql": {
                        "type": "string",
                        "description": "One statement in the chosen database's own dialect.",
                    },
                }
            ),
            handler=run_readonly_sql,
        ),
        Tool(
            name="translate_sql",
            description=(
                "Translate SQL between dialects with a rule-based translator. Fast and "
                "a good first draft, but it leaves constructs it has no rule for "
                "unchanged without saying so, and the output is not checked against "
                "any database. Treat the result as unverified: read it, fix what it "
                "missed, and confirm with compare_query_results."
            ),
            input_schema=_object_schema(
                {
                    "sql": {"type": "string", "description": "The SQL to translate."},
                    "source_dialect": {
                        "type": "string",
                        "description": "Dialect of the input, e.g. oracle, snowflake.",
                    },
                    "target_dialect": {
                        "type": "string",
                        "description": "Dialect to produce, e.g. postgres, bigquery.",
                    },
                }
            ),
            handler=translate_sql,
        ),
        Tool(
            name="compare_query_results",
            description=(
                "Run one query on the source and one on the target, then compare the "
                "rows. Returns a status: 'match' (same rows, order ignored), 'mismatch' "
                "(with how they differ), 'source_error' or 'target_error' (with the "
                "database's message), or 'not_comparable' (too many rows). This is the "
                "way to verify a translation. A match is evidence, not proof: it "
                "covers only the rows in the current data."
            ),
            input_schema=_object_schema(
                {
                    "source_sql": {
                        "type": "string",
                        "description": "The original query, in the source dialect.",
                    },
                    "target_sql": {
                        "type": "string",
                        "description": "The translated query, in the target dialect.",
                    },
                }
            ),
            handler=compare_query_results,
        ),
    ]
