# Milestone 5 - RAG latency benchmark

- LLM answered 24/24 runs
- Runs that waited on the free-tier quota (HTTP 429): 0
- Citation coverage: 173/175 answer statements carry a valid source tag; invented tags: 0
- Date: 2026-11-07 23:26  |  LLM: `openai/gpt-oss-120b` via `https://api.groq.com/openai/v1`
- Runs: 24 (3 rounds x 8 questions); connections and the embedding model are warmed once at startup (like a running service)
- Retrieval sources run in parallel threads, so `retrieval` is less than the sum of SQL + document + vector
- Pause of 20.0s between questions (free-tier rate limit) is NOT included in latency

## End-to-end and per-stage latency (ms)

| Stage | Mean | P50 | P95 | P99 | Max | Share of total (mean) |
|---|---|---|---|---|---|---|
| routing | 0 | 0 | 0 | 1 | 1 | 0% |
| sql | 34 | 33 | 47 | 50 | 51 | 2% |
| document | 32 | 32 | 36 | 37 | 37 | 2% |
| vector | 417 | 361 | 684 | 792 | 824 | 28% |
| retrieval | 418 | 361 | 685 | 792 | 824 | 28% |
| prompt | 0 | 0 | 0 | 0 | 0 | 0% |
| generation | 1080 | 1066 | 1347 | 1812 | 1948 | 72% |
| total | 1498 | 1470 | 1945 | 2287 | 2388 | 100% |

**Median end-to-end latency: 1.47 s  |  P95 1.95 s  |  24/24 runs under the 5-second target (100%).**

## Per question (median of rounds, ms)

| # | Question | Retrieval | Generation | Total | Cited |
|---|---|---|---|---|---|
| 1 | What are common failure modes for Model X-7000 batteries in high-temperature environments? | 341 | 1162 | 1502 | 7/8 |
| 2 | Which suppliers provide lithium cells for our commercial energy storage units? | 464 | 1296 | 1792 | 10/10 |
| 3 | Show me service history for equipment unit SN-4829 and related warranty claims | 331 | 860 | 1191 | 5/5 |
| 4 | Show service history and warranty claims for equipment EQ-6269 | 362 | 1026 | 1453 | 6/7 |
| 5 | What does fault code ERR_INV_404 mean and which units reported it? | 686 | 1129 | 1917 | 6/6 |
| 6 | Why is unit SN-C12-0055 raising cooling faults and what should the technician check? | 338 | 1162 | 1500 | 7/7 |
| 7 | Which parts have a single-source supplier risk? | 411 | 1019 | 1335 | 10/10 |
| 8 | What are the emergency response steps for a confirmed thermal runaway event? | 334 | 822 | 1204 | 7/7 |
