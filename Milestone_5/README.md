# Milestone 5 — Multi-Model Integration & Hybrid RAG Retrieval Engine

A field engineer types a question in plain English. The system pulls evidence from three different
databases, sends that evidence to an LLM, and returns an answer where **every sentence carries a citation**
back to a specific record.

```
                       ┌──────────────── hybrid_retrieval.py ────────────────┐
 question ──► router ──┤  MariaDB  (SQL)     equipment, service logs, warranty, parts/suppliers
 (rag.py)   (regex +   │  MongoDB  (document) BMS telemetry: fault codes, temperatures, components
             keywords) │  ChromaDB (vector)  25 manuals/SOPs/incident reports (Milestone 4 design)
                       └──── run in parallel, every record tagged [SQL1] [MDB2] [VEC3] ─────┘
                                            │
                prompts/ templates ──► LLM (free Groq gpt-oss-120b) ──► cited answer
                                            │
                        citation check + provenance table + latency breakdown
```

**Instructor changes that apply here**
- **Graph database is NOT required** (Nov 2 correction). Rubric items about the graph database get full credit.
  Supplier questions are still answered: `parts_inventory` (SQL) gives suppliers and single-source risk, and the
  supply-chain PDFs (vector) give tier-1/2/3 context.
- **Vector database stays** — rebuilt here with the same Milestone 4 design (ChromaDB, HNSW, all-MiniLM-L6-v2).

## Where each rubric item is covered

| Rubric item | What to point at |
|---|---|
| Document store (telemetry) | `load_telemetry.py`, `config/docker-compose.yml` — MongoDB, nested `telemetry{}` sub-document, `flagged_components[]` array, indexes on timestamp, equipment_id+timestamp, fault_code+timestamp, model+severity, components, temperature; `explain()` output proves index use |
| Graph database | Descoped by instructor → full credit |
| Hybrid retrieval | `hybrid_retrieval.py` — one class per source, query router picks sources by question type (procedure questions skip SQL/telemetry, supplier questions skip telemetry, etc.), sources run in parallel, a source being down doesn't crash the pipeline |
| RAG pipeline | `rag.py` + `prompts/` — structured prompt, temperature 0.1, citation rules, rate-limit retry, offline fallback answer if the LLM is unreachable |
| Provenance | Every record has database, record ID, document/table, timestamp and a verify command; `rag.py` checks every statement is cited and flags invented tags; `verify.py` re-fetches any cited record live |
| Latency < 5 s | `benchmark.py` → `results/benchmark.md` (P50/P95/P99, retrieval vs generation per stage) |
| Test queries with provenance | `results/test_query_results.md` |

## Files

| File | Purpose |
|---|---|
| `setup.sh` | One command: installs MariaDB (re-uses `Milestone_1/bootstrap_gigavolt_m1.sh`), MongoDB (Docker), Python env, downloads data, loads everything |
| `sql/rag_setup.sql` | Retrieval indexes on the Silver ERP tables + read-only `rag_reader` user |
| `config/docker-compose.yml` | MongoDB document-store configuration |
| `load_telemetry.py` | Loads `telemetry_data.json` into MongoDB, builds indexes, runs sample diagnostic queries |
| `build_vector_store.py` | PDFs → sentence-aware chunks → embeddings → ChromaDB (HNSW, cosine) |
| `hybrid_retrieval.py` | Query router + SQL / document / vector retrievers |
| `rag.py` | End-to-end RAG: retrieval → prompt → LLM → cited answer (single question or interactive) |
| `verify.py` | Re-fetches cited records from their databases (provenance demo) |
| `benchmark.py` | Runs 8 test questions × 3 rounds, writes latency stats + answers with provenance |
| `prompts/system_prompt.txt`, `prompts/answer_template.txt` | Prompt templates |

---

## Step by step (AWS Academy Learner Lab)

### 1. Launch the instance (you already did this)
Ubuntu Server 26.04, **t3.medium** (4 GB RAM), 20 GB gp3, key pair `vockey`, SSH allowed. No other ports
are needed — MariaDB and MongoDB only listen on localhost.

### 2. Push this folder to GitHub (from your Mac)
```bash
cd "/Users/ameydhote/D drive/Tamu_Assignments/Fall Sem 2026/Adv Data/Milestones"
git add Milestone_5 && git commit -m "Milestone 5: hybrid RAG pipeline" && git push
```

### 3. Connect and clone
Download `labsuser.pem` from Learner Lab → AWS Details, then:
```bash
chmod 400 ~/Downloads/labsuser.pem
ssh -i ~/Downloads/labsuser.pem ubuntu@<EC2-public-IP>
git clone https://github.com/ameyZd/project-gigavolt-data-lakehouse.git
cd project-gigavolt-data-lakehouse
```

### 4. Run setup (about 5–8 minutes)
```bash
bash Milestone_5/setup.sh
```
It ends by printing MongoDB index usage for sample fault queries and a vector-search smoke test.
Re-running it is safe.

### 5. Add a free LLM key
Create a key at https://console.groq.com/keys (free, no card), then:
```bash
cd Milestone_5
nano .env                      # paste it after LLM_API_KEY=
source .venv/bin/activate
```

### 6. Ask questions
```bash
python rag.py "What are common failure modes for Model X-7000 batteries in high-temperature environments?"
python rag.py --show-context "Which suppliers provide lithium cells for our commercial energy storage units?"
python rag.py                  # interactive mode; type exit to quit
```
Each run prints: routing decision → answer with `[TAG]` citations → provenance table → citation check →
latency breakdown, and is saved to `results/runs/`.

### 7. Verify provenance
```bash
python verify.py SQL1 MDB1 VEC1   # re-fetches those records from MariaDB / MongoDB / ChromaDB
```

### 8. Benchmark (about 5 minutes)
```bash
python benchmark.py               # 3 rounds x 8 questions, 10 s pause between questions for the free tier
```
Commit `results/benchmark.md`, `results/benchmark.csv` and `results/test_query_results.md`. If you don't want
to set up GitHub auth on EC2, copy them to your Mac instead:
```bash
scp -i ~/Downloads/labsuser.pem "ubuntu@<EC2-public-IP>:project-gigavolt-data-lakehouse/Milestone_5/results/*.{md,csv}" Milestone_5/results/
```

### After a Learner Lab restart
Nothing to redo. MariaDB (systemd) and MongoDB (`restart: unless-stopped`) start on boot; Chroma is files on
disk. Just `cd project-gigavolt-data-lakehouse/Milestone_5 && source .venv/bin/activate`.
The public IP changes after each restart.

## Design choices

- **Free and light.** The LLM is Groq's free tier (fast hosted openai/gpt-oss-120b, about 2 s per answer).
  Embeddings use ChromaDB's built-in ONNX all-MiniLM-L6-v2, so there's no PyTorch download on the 20 GB disk.
  Any OpenAI-compatible endpoint works by changing `.env`: OpenRouter free models, or a local Ollama.
- **Preventing hallucination.** The prompt allows only facts from context. Every statement must carry a tag.
  When a lookup finds nothing, that empty result is itself cited (for example, "no ERP record for SN-4829").
  The pipeline flags uncited statements and invented tags.
- **Data reality.** Telemetry serials (`SN-X7-0094`) use a different format from ERP serials (`SN-X7000-68449`),
  so units can't be joined record-to-record. The router joins on **model** (X-7000) instead and says so when a unit
  exists only in telemetry.
- **Latency.** Connections and the embedding model warm up once at startup. The three stores are queried in
  parallel (about 100 ms total), so almost all end-to-end time is LLM generation.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `model_decommissioned` / 404 from the LLM | Pick a current model at console.groq.com/docs/models and set `LLM_MODEL` in `.env` |
| `ok (after rate-limit retry)` or 429s in benchmark | Free-tier limit — use `python benchmark.py --pause 15` |
| Answer says "LLM unavailable" | Key missing or wrong in `.env`; the evidence is still shown with citations |
| MongoDB connection refused | `sudo docker start gigavolt-mongo` |
| MariaDB access denied | `sudo mariadb < sql/rag_setup.sql` |

## Submission checklist
- Unlisted YouTube link (3–5 min, script in `VIDEO_SCRIPT.md`)
- Code pasted into the Word document: `hybrid_retrieval.py`, `rag.py`, `load_telemetry.py`, `build_vector_store.py`,
  `benchmark.py`, `verify.py`, `setup.sh`, `sql/rag_setup.sql`, `config/docker-compose.yml`, `prompts/*`
- `results/benchmark.md` and `results/test_query_results.md` (test results with provenance mapping)
- The 4 Ds for AI fluency (Delegation, Description, Discernment, Diligence)
