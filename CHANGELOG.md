# Changelog

Every entry that touches agent behaviour records its effect on eval scores.
Format: what changed → score before → score after → cost per question.

## Unreleased

### Week 2 — translation eval and baseline

| Date | Change | Translation eval (pass / total) | Cost per question |
|---|---|---|---|
| 2026-11-14 | Baseline: sqlglot alone, Oracle to PostgreSQL | **22 / 42 (52%)** | $0 (no model) |

Baseline details: 17 translations fail to run on PostgreSQL, 3 run and return
different rows, 22 return the same rows as Oracle (1 of those on zero rows).

Why the 20 fail:
- 6 call a user function that has not been migrated to PostgreSQL
- 3 read a view that has not been migrated
- 3 use `ROWNUM`
- 3 use `CONNECT BY` or `KEEP (DENSE_RANK ...)`: syntax errors
- 2 use date arithmetic or `MONTHS_BETWEEN` that PostgreSQL types reject
- 3 run but are wrong: `(+)` turned into an inner join twice, `PIVOT` dropped once

Known weakness of the eval itself: `customers_missing_address` passes although
its `(+)` join was turned into an inner join. Every customer in the data has an
address, so both joins return the same rows. The data needs orphan rows to
catch this.

### Week 1 — scaffolding and data
- Project scaffolding: uv, ruff, mypy, pytest, Docker Compose (Postgres + pgvector, Oracle Free), CI.
- Eval impact: none (no evals yet).
