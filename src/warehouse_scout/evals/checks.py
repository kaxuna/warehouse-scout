"""Deterministic checks on a free-text answer.

An agent answers in sentences. These functions decide, without a model,
whether an answer contains the fact that makes it right: a name, a number,
a yes or a no. They are strict about the fact and blind to the wording
around it.

Questions whose quality cannot be reduced to a fact ("explain the risk ...")
are not handled here; they go to the judge model.
"""

import re

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_YES_NO = re.compile(r"\b(yes|no)\b", re.IGNORECASE)


def mentions(answer: str, name: str) -> bool:
    r"""True if `name` appears as a whole word, ignoring case.

    `\b` treats `_` and digits as part of a word, so ITEM is not found inside
    ITEM2 or DIM_ITEM.
    """
    return re.search(rf"\b{re.escape(name)}\b", answer, re.IGNORECASE) is not None


def names_all(answer: str, expected: list[str]) -> bool:
    return all(mentions(answer, name) for name in expected)


def names_any(answer: str, expected: list[str]) -> bool:
    return any(mentions(answer, name) for name in expected)


def numbers_in(answer: str) -> list[float]:
    """Every number in the text; "288,464" is read as 288464."""
    return [float(match.replace(",", "")) for match in _NUMBER.findall(answer)]


def has_number(answer: str, expected: float, tolerance: float = 0.0) -> bool:
    return any(abs(number - expected) <= tolerance for number in numbers_in(answer))


def yes_or_no(answer: str) -> bool | None:
    """The first "yes" or "no" in the answer as a bool, or None if there is neither."""
    match = _YES_NO.search(answer)
    return None if match is None else match.group(1).lower() == "yes"


def is_yes_no(answer: str, expected: bool) -> bool:
    return yes_or_no(answer) is expected
