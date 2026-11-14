"""Small helpers for building SQL text safely."""

import re

_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def checked_identifier(name: str) -> str:
    """Return `name` unchanged if it is a plain SQL identifier, else raise ValueError.

    Table and column names cannot be bind parameters, so they are formatted
    into SQL text. Accepting only letters, digits and underscores keeps
    anything unexpected out of the statement.
    """
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"Not a plain SQL identifier: {name!r}")
    return name
