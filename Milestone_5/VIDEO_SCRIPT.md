# Milestone 5 Video Script (target 4:30, hard limit 5:00)

## Before you hit record
1. SSH into the EC2 instance. Run `cd ~/project-gigavolt-data-lakehouse/Milestone_5 && source .venv/bin/activate`.
2. Run `python benchmark.py` **before** recording (it takes about 5 minutes). In the video you only open its results.
3. Run one warm-up question so nothing downloads on camera:
   `python rag.py "Which parts have a single-source supplier risk?"`
4. Set the terminal font to 16–18 pt and the window to about 150 columns wide. Run `clear`.
5. Open a second tab or window with `README.md` on GitHub (for the architecture diagram), and `results/benchmark.md`.

---

### 0:00 – 0:25 | Intro + architecture
**ON SCREEN:** GitHub `Milestone_5/README.md`, scrolled to the architecture diagram.

**SAY:**
"Hi, I'm Amey. This is Milestone 5 of Project Gigavolt, a hybrid RAG engine running on an AWS EC2 Ubuntu instance.
A field engineer asks a question in plain English. My router decides which databases can help, and it pulls
evidence from MariaDB for ERP data, MongoDB for IoT telemetry, and ChromaDB for the technical manuals. Then an
LLM writes an answer where every sentence is cited back to a specific record. The graph database was removed from
scope in Professor Gomillion's November 2nd announcement, so supplier questions use the parts table and the
supply-chain reports instead."

### 0:25 – 1:05 | Document store (MongoDB)
**ON SCREEN:** run `python load_telemetry.py`. Then scroll up slowly through the output: the index list, the three
sample queries showing `index=ix_...`, and the fault-code summary.

**SAY:**
"First, the document store. Each BMS log is one MongoDB document. I kept the nested structure, so the readings
are a `telemetry` sub-document and the flagged components are an array. The log ID is the primary key, which
makes reloads idempotent. I indexed by timestamp, by equipment ID plus timestamp, by fault code plus timestamp,
by model plus severity, and by flagged components. The explain plan shows each diagnostic query uses its index.
For example, all ERR_INV_404 overcurrent events come back in under a millisecond using `ix_fault_time`. The
aggregation at the bottom summarizes failures by fault code."

### 1:05 – 2:20 | Live query 1: all three sources
**ON SCREEN:** type and run:
`python rag.py "What are common failure modes for Model X-7000 batteries in high-temperature environments?"`
Point at each panel as you talk: routing panel → answer → provenance table → citation check → latency line.

**SAY:**
"Now a live question. The router found the model X-7000 and recognized failure and temperature intent, so it
sent the question to all three stores in parallel.
SQL gives the ERP fleet size and the most common repair resolutions for X-7000 from the service logs.
MongoDB gives the fault codes for X-7000 units, with average and max temperatures and the components flagged.
ChromaDB returns the most similar manual and incident-report chunks.
In the answer, each bullet ends with tags like SQL2, MDB1 or VEC3. The provenance table maps each tag to the
database, the exact record ID, the document or table, and a timestamp. The bold rows are the ones the answer
actually used. The citation check confirms every statement is cited and no tag was invented. At the bottom you
can see retrieval took about a third of a second and generation about one second, so the whole answer arrives
in about one and a half seconds, well under the five-second target."

### 2:20 – 3:00 | Live query 2: routing + hallucination prevention
**ON SCREEN:** run:
`python rag.py "Show me service history for equipment unit SN-4829 and related warranty claims"`
Point at the SQL1 row ("no matching row") and the MDB rows.

**SAY:**
"This one is from the assignment. SN-4829 appears in the telemetry, but it does not exist in the ERP tables.
Instead of inventing a service history, the SQL lookup returns an explicit 'no matching record'. That empty
result is passed to the LLM as evidence, and the answer says there's no ERP or warranty history, citing SQL1.
It then reports what the telemetry does show, citing the Mongo records. That's how I prevent hallucination:
the model may only use the context, every sentence has to cite a tag, and missing data is stated, not guessed."

### 3:00 – 3:30 | Provenance verification
**ON SCREEN:** run `python verify.py SQL1 MDB1 VEC1`.

**SAY:**
"To prove the citations are real, verify.py takes tags from the last answer and re-fetches those records live.
It runs the SQL query against MariaDB, the find query against MongoDB, and a chunk lookup in ChromaDB. You can
see each record matches what the answer claimed. That's one-to-one traceability from answer sentence to source
record."

### 3:30 – 4:00 | Supplier question (optional if time is short)
**ON SCREEN:** run `python rag.py "Which suppliers provide lithium cells for our commercial energy storage units?"`
Point at the routing panel (SQL + vector only) and "SINGLE-SOURCE RISK".

**SAY:**
"Routing changes by question type. A supplier question skips telemetry. SQL groups the lithium-cell parts by
supplier and flags any part with only one supplier as a single-source risk. The vector search adds the
tier-1-to-3 supply-chain brief."

### 4:00 – 4:35 | Benchmark
**ON SCREEN:** open `results/benchmark.md` (on GitHub or `less results/benchmark.md`). Highlight the stage table
and the bold median line.

**SAY:**
"For performance, I ran eight test questions three times each, with connections warmed once like a running
service. The LLM answered all 24 runs. The table breaks latency down by stage. SQL and MongoDB each take about
30 milliseconds, and vector search about 360, and because they run in parallel, retrieval totals about 0.36
seconds at the median. That's 28 percent of the time; LLM generation, at about 1.07 seconds, is the other
72 percent. End to end, the median is 1.47 seconds, P95 is 1.95 seconds, and P99 is 2.29 seconds, so all
24 out of 24 runs finished under the five-second target. For provenance, 173 of 175 answer statements carry a
valid source tag, which is 99 percent, and the model invented zero tags. All the answers with their full
provenance tables are in test_query_results.md."

### 4:35 – 4:50 | Close
**ON SCREEN:** scroll back to the X-7000 answer from query 1, with its provenance table visible.

**SAY:**
"To sum up: a field engineer now gets one answer that combines the unit's ERP service history, its live
telemetry faults, and the right manual page, in under two seconds, with every fact traceable to a record. When
the data isn't there, the system says so instead of guessing. In Milestone 6, I'll automate this pipeline end to
end and tune it further. Thanks for watching."

---
**Timing tips:** If you're running long, cut the 3:30 supplier segment. Don't scroll through the full
`--show-context` prompt on camera; just say "the assembled prompt is printed with --show-context".
