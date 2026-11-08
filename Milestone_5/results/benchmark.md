# Milestone 5 - RAG latency benchmark

- LLM answered 24/24 runs
- Runs that waited on the free-tier quota (HTTP 429): 0
- Citation coverage: 182/182 answer statements carry a valid source tag; invented tags: 0
- Date: 2026-11-08  |  LLM: `openai/gpt-oss-120b` via `https://api.groq.com/openai/v1`
- Runs: 24 (3 rounds x 8 questions); connections and the embedding model are warmed once at startup (like a running service)
- Retrieval sources run in parallel threads, so `retrieval` is less than the sum of SQL + document + vector
- Pause of 20.0s between questions (free-tier rate limit) is NOT included in latency

## End-to-end and per-stage latency (ms)

| Stage | Mean | P50 | P95 | P99 | Max | Share of total (mean) |
|---|---|---|---|---|---|---|
| routing | 0 | 0 | 0 | 0 | 1 | 0% |
| sql | 35 | 31 | 50 | 50 | 50 | 2% |
| document | 35 | 33 | 47 | 54 | 56 | 2% |
| vector | 397 | 357 | 671 | 719 | 733 | 26% |
| retrieval | 398 | 357 | 671 | 720 | 733 | 26% |
| prompt | 0 | 0 | 0 | 0 | 0 | 0% |
| generation | 1107 | 1050 | 1700 | 1927 | 1993 | 74% |
| total | 1505 | 1445 | 2171 | 2558 | 2669 | 100% |

**Median end-to-end latency: 1.45 s  |  P95 2.17 s  |  24/24 runs under the 5-second target (100%).**

## Per question (median of rounds, ms)

| # | Question | Retrieval | Generation | Total | Cited |
|---|---|---|---|---|---|
| 1 | What are common failure modes for Model X-7000 batteries in high-temperature environments? | 368 | 1034 | 1352 | 8/8 |
| 2 | Which suppliers provide lithium cells for our commercial energy storage units? | 356 | 1654 | 1986 | 11/11 |
| 3 | Show me service history for equipment unit SN-4829 and related warranty claims | 362 | 800 | 1176 | 5/5 |
| 4 | Show service history and warranty claims for equipment EQ-6269 | 327 | 1147 | 1472 | 7/7 |
| 5 | What does fault code ERR_INV_404 mean and which units reported it? | 676 | 1543 | 2187 | 6/6 |
| 6 | Why is unit SN-C12-0055 raising cooling faults and what should the technician check? | 314 | 1066 | 1428 | 7/7 |
| 7 | Which parts have a single-source supplier risk? | 359 | 996 | 1350 | 10/10 |
| 8 | What are the emergency response steps for a confirmed thermal runaway event? | 322 | 719 | 1089 | 8/8 |
