"""Groups, pairs and how to score a set of predicted duplicates.

Duplicates are naturally groups ("these four tables are the same"), but
groups are awkward to score: is a predicted group of three out of four right
or wrong? Pairs are simple: every two members of a group form one pair, and
a predicted pair is either in the answer key or not.
"""

from collections.abc import Iterable
from itertools import combinations

Pair = frozenset[str]


def pairs_of(groups: Iterable[Iterable[str]]) -> set[Pair]:
    """Every unordered pair of names that share a group."""
    return {frozenset(pair) for group in groups for pair in combinations(sorted(group), 2)}


def groups_of(pairs: Iterable[Pair]) -> list[list[str]]:
    """Merge pairs into groups: if A=B and B=C then {A, B, C}. Sorted for stable output."""
    group_of: dict[str, set[str]] = {}
    for pair in pairs:
        merged = set(pair)
        for name in pair:
            merged |= group_of.get(name, set())
        for name in merged:
            group_of[name] = merged
    # Several names share one set object; id() picks each distinct set once.
    distinct = {id(group): group for group in group_of.values()}
    return sorted(sorted(group) for group in distinct.values())


def precision_recall(predicted: set[Pair], expected: set[Pair]) -> dict[str, float]:
    """Precision: share of predicted pairs that are right.
    Recall: share of the real pairs that were found.
    F1: their harmonic mean; high only when both are high."""
    correct = len(predicted & expected)
    precision = correct / len(predicted) if predicted else 0.0
    recall = correct / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "predicted": len(predicted),
        "correct": correct,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }
