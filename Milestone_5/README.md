# Project Gigavolt — Milestone 5: Multi-Model Integration & Hybrid RAG Retrieval Engine

> ISTM 622 · Advanced Data Management · Fall 2026 · Amey Dhote

Milestone 5 turns the lakehouse into an **Enterprise Knowledge Engine**: a field engineer types a
question in plain English and gets an answer that is assembled from three different databases, generated
by an LLM, and **cited down to the exact record**, in under five seconds.

| Source | Engine | What it contributes | Citation tag |
|---|---|---|---|
| Structured ERP | MariaDB, single instance (Milestone 1–2 data), `gigavolt_silver` via `vw_rag_*` views | equipment metadata, warranty status, service history, warranty claims, parts → suppliers | `[S#]` |
| IoT telemetry | **MongoDB** `gigavolt_iot.bms_telemetry` (new in M5) | BMS fault codes, sensor readings, flagged components, event sequences | `[D#]` |
| Technical documents | ChromaDB HNSW (Milestone 4) + all-MiniLM-L6-v2 | manuals, SOPs, ECOs, incident reports, datasheets, supplier reports | `[V#]` |
| Supply-chain graph | ~~Neo4j~~ | **Removed from M5 by the instructor's Nov 2 correction** (rubric item → full credit). Supplier context comes from ERP parts data and the supplier-report PDFs instead. | `[G#]` reserved |

## Architecture and data flow

```mermaid
flowchart LR
  Q["Natural-language question"] --> R["Query router<br/>entities + intents"]
  R -->|"metadata filters"| S[("MariaDB<br/>vw_rag_* views")]
  R -->|"indexed find / aggregate"| D[("MongoDB<br/>bms_telemetry")]
  R -->|"embed + HNSW"| V[("ChromaDB<br/>Milestone 4")]
  D -->|"fault + component codes"| H["Multi-hop<br/>vector search"]
  H --> V
  S --> C["Context assembly<br/>S# D# V# tags"]
  D --> C
  V --> C
  C --> P["Prompt template<br/>grounding rules"]
  P --> L["LLM<br/>Claude Haiku 5.5"]
  L --> X["Provenance validator"]
  X --> O["Cited answer +<br/>provenance map +<br/>latency profile"]
```

1. **Route** (`gigavolt_rag/router.py`, < 1 ms). Regexes extract serial numbers, equipment IDs, models,
   fault codes and component codes; keyword rules classify intent (unit history, warranty, failure analysis,
   supply chain, telemetry, procedure). The plan says which databases to query, with which filters, and
   prints a one-line reason for every source used or skipped. A procedure question goes to ChromaDB only. A
   unit question gets an exact ERP lookup and a telemetry timeline.
2. **Retrieve in parallel** (`gigavolt_rag/retrieval.py`). MariaDB, MongoDB and ChromaDB run in three
   threads. If one source fails, the error is recorded and the answer is built from the others.
3. **Multi-hop.** Fault codes and component codes found in telemetry (e.g. `ERR_INV_404`, `INV-SCR-9901`)
   drive a second vector search. That pulls in the engineering change order or incident report about those
   exact codes.
4. **Assemble context.** Each record gets a citation tag plus its metadata (database, record ID, document
   and page, timestamp). Evidence of absence ("no ERP row for SN-4829") is a citable record too, so the
   model can say "not found" instead of guessing.
5. **Generate** (`prompts/system_prompt.txt`, `gigavolt_rag/llm.py`). Claude Haiku 5.5 runs with
   low effort and thinking disabled for latency. OpenAI, a local Ollama model, or an
   **extractive no-LLM mode** can be swapped in with one `.env` setting.
6. **Validate provenance** (`gigavolt_rag/provenance.py`). Every sentence must carry a tag that exists in
   the context. The validator reports citation coverage, invented tags, and **numbers that do not appear in
   the cited record**. `scripts/verify_citation.py` re-runs the stored query against the live database to
   prove a citation.

## Why each database (polyglot persistence)

- **MariaDB for ERP facts.** These are relational, constrained and transactional. Exact filters (serial,
  model, warranty status) and aggregates are what SQL does best. The RAG account can `SELECT` four views
  only, continuing Milestone 2's least-privilege model. Customer identities and part costs never reach the
  LLM.
- **MongoDB for telemetry.** BMS events are semi-structured: a nested `telemetry` sub-document, a
  variable-length `flagged_components` array, and optional fault codes. A document store keeps each event
  intact. Its compound indexes (`equipment_id+timestamp`, `fault_code+timestamp`,
  `model_code+is_fault+timestamp`), the multikey index on the components array, the nested-field index on
  `telemetry.temperature_c`, and a text index on notes serve every diagnostic query with an IXSCAN. A
  `$jsonSchema` validator plus a Python normalizer quarantine bad records instead of dropping them silently.
- **ChromaDB for documents.** Meaning-based search over PDFs that SQL cannot do. The Milestone 4 HNSW
  collection is reused unchanged (cosine, M=16, ef_construction=200, ef_search=64).
- **Why not a graph DB.** Removed by the instructor. The supply-chain question is still answered: ERP parts
  give supplier and manufacturer, telemetry gives which components are failing, and supplier reports give the
  tier-1/2/3 chain and single-source risks.

## How hallucination is prevented

| Layer | Mechanism |
|---|---|
| Retrieval | Exact-match lookups only (SN-4829 is never "fuzzy-matched" to DISP-14829). Empty lookups become explicit absence records. Low-similarity chunks (cosine distance > 0.80) are dropped. An out-of-scope question returns "no context" with no LLM call. |
| Prompt | The model may use only the tagged context. Every sentence needs a tag. Numbers must be copied exactly, sources must not be conflated, and safety advice must come from a cited SOP. |
| Generation | Low effort, no extended thinking, short answer budget, fixed output format (Answer / Key findings / Gaps). |
| Post-check | Citation coverage %, invalid-tag detection, and a numeric-grounding check, all shown with every answer. |
| Audit | `verify_citation.py` re-executes the SQL, Mongo pipeline or Chroma `get` behind any tag. |
| Failure handling | LLM timeout or bad key → retry, then an extractive answer that is 100% cited. A database outage → partial answer with a warning. |

## Folder layout

```text
Milestone_5/
├── config/            mongod.conf, docker-compose.mongodb.yml
├── setup/             00 MariaDB + ERP data · 01 install MongoDB · 02 download telemetry · 03 MariaDB RAG views + SELECT-only user
├── sql/               01_rag_views_and_indexes.sql
├── gigavolt_rag/      router, retrieval, sources/(sql|vector|document), prompts, llm, provenance, pipeline, display
├── prompts/           system_prompt.txt, answer_template.txt   (sample prompt templates)
├── scripts/           01 load telemetry · 02 telemetry queries · 03 health check · ask.py
│                      04 test queries · 05 latency benchmark · verify_citation.py
├── tests/             test_offline.py (22 offline tests), test_queries.json, fixtures/
└── output/            test_results/ (answers + provenance maps), benchmark/ (P50/P95/P99 report + chart)
```

## Quick start (one EC2 instance)

```bash
cd ~/project-gigavolt-data-lakehouse/Milestone_5
sudo bash setup/00_install_mariadb_and_load_erp.sh   # one MariaDB server + Milestone 1–2 data (no Galera)
sudo bash setup/01_install_mongodb.sh
bash setup/02_download_telemetry.sh
cp .env.example .env && nano .env             # MariaDB password + LLM API key
pip install -r requirements.txt               # inside your Milestone 4 venv
python scripts/01_load_telemetry_mongodb.py --drop
python scripts/02_telemetry_queries.py
sudo bash setup/03_create_mariadb_rag_access.sh
python scripts/03_check_sources.py --llm
python scripts/ask.py -i
python scripts/04_run_test_queries.py --quiet
python scripts/05_benchmark_latency.py --repeats 3
```

## Results

These files are generated on EC2 by the last two commands above:

- [`output/test_results/README.md`](output/test_results/README.md): each evaluation question with the
  sources used, citation coverage, invalid tags, numeric grounding and latency, linked to a per-question
  provenance map.
- [`output/benchmark/latency_report.md`](output/benchmark/latency_report.md): P50/P95/P99 per stage and
  end-to-end, retrieval share vs. generation share, and throughput. Includes `latency_profile.png`.

## Trade-offs

- **Rule-based routing** is deterministic, explainable and takes about 0 ms, but new phrasing needs new
  keywords. An LLM router would generalize better at the cost of roughly 1 s per question.
- **Aggregates vs. raw rows.** Model-level questions get grouped SQL and Mongo summaries with sample record
  IDs. That keeps the prompt small (fast, cheap) while every number stays traceable to a re-runnable query.
- **Cloud LLM vs. local.** A hosted Haiku-class model meets the 5 s target on a CPU-only EC2 instance. A
  local Ollama model keeps data on the instance but is several times slower on CPU.
- **One EC2 instance for all three databases.** MariaDB, MongoDB and ChromaDB share one host, so every read is on localhost (low latency). Milestone 5 does not need the Milestone 3 Galera cluster; high availability was that milestone's topic. In production, the
  document store would get its own replica set.
