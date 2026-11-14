# Warehouse Scout

AI assistant for data-warehouse migration assessment. Point it at a warehouse and ask:

- **What matters?** Which tables and queries carry the workload, and which are dead or duplicated.
- **What breaks?** Which queries will not survive the move to the target dialect.
- **Fix it.** Rewrite a query for the target, run both versions, and report how confident the result is.

> Status: week 1 of 6 — scaffolding and data. Nothing below the "Quick start" line is measured yet.

## How it works

```
 Source warehouse ──extract──▶ Scout's Postgres ◀──search── Agent (Claude) ──▶ answer
 (Oracle)                      schema snapshot                │
                               query log                      ├─ translate_sql
                               embeddings (pgvector)          ├─ run_readonly_sql
                                                              └─ compare_results ──▶ target (Postgres)
```

- **First verified pair:** Oracle → Postgres. Both run locally in Docker, so every check executes against real engines.
- **Design goal:** any-to-any. Source, target, translator and embedder each sit behind an interface.

## Quick start

Requirements: Docker, [uv](https://docs.astral.sh/uv/), make.

```bash
make sync     # install dependencies
make up       # start Postgres + Oracle (creates .env from .env.example on first run)
make check    # lint, type check, unit tests
make load     # generate TPC-DS and copy it into Oracle
make noise    # add ~100 junk tables, views and duplicate functions
make querylog # write a synthetic 30-day query history to data/query_log.jsonl
make copy-target  # copy all Oracle tables into Postgres, the migration target
make eval-translation  # score a translator against real results on both databases
```

Add your `ANTHROPIC_API_KEY` to `.env` (git-ignored; never commit it), then:

```bash
make hello    # one Claude call: translate an Oracle query, print tokens, latency, cost
make ask Q="Translate to PostgreSQL: SELECT * FROM item WHERE ROWNUM <= 5"   # the agent
```

## Results

Translation eval: each read query of the workload runs on Oracle, its translation runs on
PostgreSQL, and the rows are compared. A case passes only if both return the same rows.

| Date | System | Pass / total | Cost / question |
|---|---|---|---|
| 2026-11-14 | sqlglot alone (baseline, after eval hardening) | 21 / 42 (50%) | $0 |

Run history: `evals/history/translation.jsonl`. What each change did: [CHANGELOG](CHANGELOG.md).

## Known limitations

_To be filled as they are measured._

## Roadmap

| Week | Milestone |
|---|---|
| 1 | Scaffolding, TPC-DS in Oracle, synthetic query log, first Claude call |
| 2 | Agent loop with `run_readonly_sql` + `translate_sql`; mini eval harness |
| 3 | RAG with pgvector + `search_schema`; duplicate detection |
| 4 | Full golden set, LLM judge, eval runner, CI gate, baseline score |
| 5 | MCP server, cost/latency logging, prompt caching, LangGraph repair pipeline |
| 6 | Guardrails + injection test, Cloud Run deploy, write-up |
