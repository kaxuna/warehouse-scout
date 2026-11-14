"""Every template must be real Oracle SQL against the real schema.

Oracle parses each statement without running it: syntax, table names, column
names and function signatures are all checked. This is what stops the
"Oracle" workload from drifting into SQL that Oracle would reject.

Needs `make load` and `make noise` first.
"""

import random
from collections.abc import Iterator

import oracledb
import pytest

from warehouse_scout.config import get_settings
from warehouse_scout.db import connect_oracle
from warehouse_scout.ingest.query_log import render
from warehouse_scout.ingest.query_templates import QUERY_TEMPLATES, QueryTemplate

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def cursor() -> Iterator[oracledb.Cursor]:
    # scope="module": one connection for all the parametrised tests in this file.
    with connect_oracle(get_settings()) as connection, connection.cursor() as cur:
        yield cur


@pytest.mark.parametrize("template", QUERY_TEMPLATES, ids=lambda template: template.id)
def test_template_parses_on_oracle(template: QueryTemplate, cursor: oracledb.Cursor) -> None:
    cursor.parse(render(template, random.Random(1)))


def test_the_parser_really_checks_column_names(cursor: oracledb.Cursor) -> None:
    # Guards the tests above: if parse() ever stopped validating, they would
    # all pass while proving nothing.
    with pytest.raises(oracledb.DatabaseError, match="ORA-00904"):
        cursor.parse("SELECT no_such_column FROM item")
