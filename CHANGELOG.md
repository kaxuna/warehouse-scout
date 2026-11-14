# Changelog

Every entry that touches agent behaviour records its effect on eval scores.
Format: what changed → score before → score after → cost per question.

## Unreleased

### Week 3 — retrieval

Retrieval eval: 32 questions, each with the objects that answer it. Is a right
answer in the top 5 search hits?

| Date | Embedder | Kind filter | Recall@1 | Recall@5 | MRR |
|---|---|---|---|---|---|
| 2026-11-14 | Word counts (baseline) | no | 6% | 9% | 0.08 |
| 2026-11-14 | Word counts (baseline) | yes | 12% | 38% | 0.22 |
| 2026-11-14 | bge-small, local | no | 59% | 81% | 0.65 |
| 2026-11-14 | bge-small, local | yes | **78%** | **91%** | **0.84** |

- Neural embeddings vs word counting: recall@5 38% → 91% with the filter.
- Metadata filter on kind: recall@5 81% → 91% for the neural model.
- Agent prompt `agent_v2` adds the `search_schema` tool. Effect on the
  translation eval: not measured yet (needs an API key).

Duplicate-detection eval, scored on pairs against the planted answer key
(153 duplicate table pairs, 6 duplicate function pairs):

| Date | Kind | Method | Precision | Recall | F1 |
|---|---|---|---|---|---|
| 2026-11-15 | Tables | Same columns, row count and rows (`MINUS` both ways) | 100% | 100% | 1.00 |
| 2026-11-15 | Tables | Embedding similarity, best threshold | 60% | 47% | 0.53 |
| 2026-11-15 | Functions | Same outputs on sample inputs | 100% | 100% | 1.00 |
| 2026-11-15 | Functions | Embedding similarity, best threshold | 80% | 67% | 0.73 |

- Where an exact answer exists (same rows, same outputs), the exact method
  wins outright. Embeddings are a way to propose candidates, not to decide.
- The embedding thresholds were chosen on this same data, which flatters them.
- Answer key corrected again: the first version knew only copies of benchmark
  tables (95 pairs). Scratch tables built from one query by four people, and
  eight identical throwaway tables, are duplicates too (58 more pairs). Scored
  against the old key, the detector would have been charged with 58 false
  positives that were in fact correct.

### Week 2 — translation eval and baseline

| Date | Change | Translation eval (pass / total) | Cost per question |
|---|---|---|---|
| 2026-11-14 | Baseline: sqlglot alone, Oracle to PostgreSQL | **22 / 42 (52%)** | $0 (no model) |
| 2026-11-14 | Eval hardened: customers without an address planted in the data. No change to any translator. | **21 / 42 (50%)** | $0 |

Baseline details: 17 translations fail to run on PostgreSQL, 3 run and return
different rows, 22 return the same rows as Oracle (1 of those on zero rows).

Why the 20 fail:
- 6 call a user function that has not been migrated to PostgreSQL
- 3 read a view that has not been migrated
- 3 use `ROWNUM`
- 3 use `CONNECT BY` or `KEEP (DENSE_RANK ...)`: syntax errors
- 2 use date arithmetic or `MONTHS_BETWEEN` that PostgreSQL types reject
- 3 run but are wrong: `(+)` turned into an inner join twice, `PIVOT` dropped once

Weakness found in the eval itself, then fixed: `customers_missing_address`
passed although its `(+)` join had been turned into an inner join. Every
customer had an address, so both joins returned the same rows. With every 25th
customer given no address, the case now fails as it should. The score went
down because the measurement got better, not because anything got worse.
22/42 was one false pass too high; 21/42 is the baseline to compare against.

Agent added (tools `run_readonly_sql`, `translate_sql`, `compare_query_results`;
hand-written loop; prompt `agent_v1`) and wired into the same eval as sqlglot.
Not yet scored: needs an API key. To score it:
`make eval-translation ARGS="--system agent"`. The run appends accuracy, tokens
and cost per question to `evals/history/translation.jsonl`; add the row above.

### Week 1 — scaffolding and data
- Project scaffolding: uv, ruff, mypy, pytest, Docker Compose (Postgres + pgvector, Oracle Free), CI.
- Eval impact: none (no evals yet).
