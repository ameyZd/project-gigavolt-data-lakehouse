"""Hybrid retrieval for the Gigavolt RAG pipeline.

One question -> a query router decides which stores can help -> each store runs in
parallel -> every record comes back as an `Evidence` object carrying full provenance
(database, table/collection, record id, document, timestamp, verify command).

  SQL      MariaDB  gigavolt_silver   equipment metadata, service history, warranty, parts/suppliers
  Document MongoDB  gigavolt.telemetry  BMS fault events and telemetry patterns
  Vector   ChromaDB gigavolt_docs     manuals, SOPs, incident reports, specs (Milestone 4)
  Graph    -- descoped by the instructor (Nov 2 announcement); supplier questions are answered
              from parts_inventory (SQL) + supply-chain reports (vector) instead.

Run directly to see only the retrieval step:  python hybrid_retrieval.py "your question"
"""
from __future__ import annotations

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")


# ----------------------------------------------------------------------------- data classes
@dataclass
class Evidence:
    source: str        # "sql" | "document" | "vector"
    database: str      # e.g. "MariaDB gigavolt_silver.service_logs"
    record_id: str     # primary key(s) of the source record(s)
    document: str      # table / collection / PDF file + page
    timestamp: str     # event date, or index time for documents
    text: str          # what the LLM sees
    verify: str        # command a reviewer can run to check the record
    tag: str = ""      # citation tag, assigned after retrieval, e.g. "SQL1"


@dataclass
class Route:
    question: str
    intents: set[str]
    entities: dict[str, list[str]]
    sources: list[str]
    reasons: list[str] = field(default_factory=list)

    def describe(self) -> str:
        ents = ", ".join(f"{k}={v}" for k, v in self.entities.items() if v) or "none"
        return f"intents={sorted(self.intents) or ['general']}; entities: {ents}; sources={self.sources}"


# ----------------------------------------------------------------------------- query router
PATTERNS = {
    "serial":   r"\bSN-[A-Z0-9]+(?:-[A-Z0-9]+)*\b",
    "equipment": r"\bEQ-\d{3,6}\b",
    "model":    r"\b[XCG]-?\d{4,5}\b",
    "fault":    r"\bERR_[A-Z]+_\d{3}\b",
    "dispatch": r"\bDISP-\d+\b",
    "claim":    r"\bCLM-\d+\b",
    "part":     r"\bPRT-\d+\b",
}
INTENT_WORDS = {
    "telemetry": ["fault", "error", "alarm", "telemetry", "sensor", "temperature", "thermal", "overheat",
                  "voltage", "trip", "critical", "bms", "failure", "failing", "failed", "raising", "reading"],
    "service":   ["service", "history", "dispatch", "repair", "maintenance", "technician", "visit", "fix"],
    "warranty":  ["warranty", "claim"],
    "supply":    ["supplier", "supply", "vendor", "sourcing", "single-source", "single source", "manufacturer",
                  "procure"],
    "failure_modes": ["failure mode", "common failure", "cause", "root cause", "why"],
    "hot":       ["high-temperature", "high temperature", "heat", "hot", "thermal", "overheat", "temperature"],
    "procedure": ["steps", "procedure", "how do", "how should", "how to", "sop", "protocol", "checklist"],
}


def route_question(question: str) -> Route:
    q_upper, q_lower = question.upper(), question.lower()
    ents = {k: sorted(set(re.findall(p, q_upper))) for k, p in PATTERNS.items()}
    ents["model"] = sorted({re.sub(r"^([XCG])-?", r"\1-", m) for m in ents["model"]})
    intents = {name for name, words in INTENT_WORDS.items() if any(w in q_lower for w in words)}

    sources, reasons = [], []
    erp_entity = any(ents[k] for k in ("serial", "equipment", "model", "dispatch", "claim", "part"))
    if "procedure" in intents and not (erp_entity or ents["fault"]):
        reasons.append("Procedure question with no unit/model/fault ID -> documentation only (skip SQL + telemetry)")
    else:
        if erp_entity or intents & {"service", "warranty", "supply"}:
            sources.append("sql")
            reasons.append("SQL: entity IDs / model / service, warranty or supplier intent -> ERP metadata filters")
        if (ents["serial"] or ents["fault"] or "telemetry" in intents
                or (ents["model"] and "failure_modes" in intents)) and intents != {"supply"}:
            sources.append("document")
            reasons.append("Document: unit serial / fault code / failure intent -> MongoDB telemetry")
    sources.append("vector")
    reasons.append("Vector: always ground the answer in manuals, SOPs and incident reports"
                   + (f" (+ exact-match filter on {ents['fault']})" if ents["fault"] else ""))
    reasons.append("Graph: descoped by instructor (Nov 2) - supplier risk uses SQL parts + supply-chain PDFs")
    return Route(question, intents, ents, sources, reasons)


# ----------------------------------------------------------------------------- SQL (MariaDB)
class SqlSource:
    REPAIR_TYPES = ("Emergency Repair", "Corrective Maintenance", "Diagnostic", "Warranty Replacement")
    COMPONENT_WORDS = {"lithium": "Lithium", "cell": "Cell", "battery": "Cell", "inverter": "Inverter",
                       "fuse": "Fus", "breaker": "Breaker", "sensor": "Sensor", "cool": "Cool",
                       "contactor": "Contactor", "cable": "Cable", "transformer": "Transformer"}

    def __init__(self) -> None:
        import pymysql
        self.db = os.getenv("MARIADB_DB", "gigavolt_silver")
        self.conn = pymysql.connect(host=os.getenv("MARIADB_HOST", "localhost"),
                                    user=os.getenv("MARIADB_USER", "rag_reader"),
                                    password=os.getenv("MARIADB_PASSWORD", ""), database=self.db,
                                    cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                                    connect_timeout=5)

    def _q(self, sql: str, args: tuple = ()) -> list[dict]:
        self.conn.ping(reconnect=True)
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    def _ev(self, table: str, rid: str, ts, text: str, where: str) -> Evidence:
        return Evidence("sql", f"MariaDB {self.db}.{table}", rid, table, str(ts or "n/a"), text,
                        f"SELECT * FROM {self.db}.{table} WHERE {where};")

    def retrieve(self, r: Route) -> list[Evidence]:
        out: list[Evidence] = []
        ids = r.entities["equipment"] + r.entities["serial"]
        if ids:
            out += self._units(ids)
        for model in r.entities["model"][:2]:
            out += self._model(model, r)
        if "supply" in r.intents:
            out += self._suppliers(r.question)
        return out

    def _units(self, ids: list[str]) -> list[Evidence]:
        ph = ",".join(["%s"] * len(ids))
        units = self._q(f"""SELECT e.equipment_id, e.serial_number, e.model, e.status, e.install_date,
                                   e.warranty_expiration, e.capacity_kw, c.company_name
                            FROM equipment_units e JOIN customers c ON c.customer_id = e.customer_id
                            WHERE e.equipment_id IN ({ph}) OR e.serial_number IN ({ph})""", tuple(ids * 2))
        if not units:
            ids_sql = ", ".join(f"'{i}'" for i in ids)
            return [self._ev("equipment_units", "no matching row", "n/a",
                             f"ERP lookup returned NO equipment_units record for {', '.join(ids)}; "
                             "this unit has no ERP service or warranty history on file.",
                             f"equipment_id IN ({ids_sql}) OR serial_number IN ({ids_sql})")]
        out = []
        for u in units:
            warranty = "ACTIVE" if u["warranty_expiration"] and str(u["warranty_expiration"]) >= time.strftime("%Y-%m-%d") else "EXPIRED"
            out.append(self._ev("equipment_units", u["equipment_id"], u["install_date"],
                                f"Unit {u['equipment_id']} (serial {u['serial_number']}), model {u['model']}, "
                                f"{u['capacity_kw']} kW, status {u['status']}, customer {u['company_name']}, installed "
                                f"{u['install_date']}, warranty expires {u['warranty_expiration']} ({warranty}).",
                                f"equipment_id = '{u['equipment_id']}'"))
            for s in self._q("""SELECT dispatch_id, dispatch_date, technician, service_type, hours_spent,
                                       labor_cost, resolution_summary, status
                                FROM service_logs WHERE equipment_id = %s
                                ORDER BY dispatch_date DESC LIMIT 5""", (u["equipment_id"],)):
                out.append(self._ev("service_logs", s["dispatch_id"], s["dispatch_date"],
                                    f"Service {s['dispatch_id']} on {s['dispatch_date']} for {u['equipment_id']}: "
                                    f"{s['service_type']} by {s['technician']}, {s['hours_spent']} h, ${s['labor_cost']} "
                                    f"labor, status {s['status']}. Resolution: {s['resolution_summary']}",
                                    f"dispatch_id = '{s['dispatch_id']}'"))
            for w in self._q("""SELECT w.claim_id, w.claim_date, w.dispatch_id, w.part_id, p.part_name, w.claim_amount,
                                       w.approved_amount, w.status, w.denial_reason
                                FROM warranty_claims w LEFT JOIN parts_inventory p ON p.part_id = w.part_id
                                WHERE w.equipment_id = %s ORDER BY w.claim_date DESC LIMIT 5""", (u["equipment_id"],)):
                out.append(self._ev("warranty_claims", w["claim_id"], w["claim_date"],
                                    f"Warranty claim {w['claim_id']} on {w['claim_date']} for {u['equipment_id']}: "
                                    f"part {w['part_id'] or 'n/a'} ({w['part_name'] or 'n/a'}), dispatch {w['dispatch_id'] or 'n/a'}, "
                                    f"claimed ${w['claim_amount']}, approved ${w['approved_amount']}, status {w['status']}"
                                    + (f", denial reason: {w['denial_reason']}" if w["denial_reason"] else "") + ".",
                                    f"claim_id = '{w['claim_id']}'"))
        return out

    def _model(self, model: str, r: Route) -> list[Evidence]:
        s = self._q("""SELECT COUNT(*) units, SUM(status = 'In Service') in_service,
                              SUM(warranty_expiration >= CURDATE()) under_warranty,
                              MIN(install_date) first_install, MAX(install_date) last_install
                       FROM equipment_units WHERE model = %s""", (model,))[0]
        out = [self._ev("equipment_units", f"aggregate model={model}", f"{s['first_install']}..{s['last_install']}",
                        f"ERP fleet for model {model}: {s['units']} units, {s['in_service'] or 0} in service, "
                        f"{s['under_warranty'] or 0} still under warranty."
                        + (" (No ERP units of this model - it appears only in telemetry/docs.)" if not s["units"] else ""),
                        f"model = '{model}'")]
        if not s["units"]:
            return out
        if r.intents & {"failure_modes", "telemetry", "service"}:
            ph = ",".join(["%s"] * len(self.REPAIR_TYPES))
            for row in self._q(f"""SELECT sl.resolution_summary, COUNT(*) n,
                                         MIN(sl.dispatch_date) first_seen, MAX(sl.dispatch_date) last_seen,
                                         GROUP_CONCAT(sl.dispatch_id ORDER BY sl.dispatch_date DESC SEPARATOR ', ' LIMIT 3) examples
                                  FROM service_logs sl JOIN equipment_units e ON e.equipment_id = sl.equipment_id
                                  WHERE e.model = %s AND sl.service_type IN ({ph})
                                  GROUP BY sl.resolution_summary ORDER BY n DESC LIMIT 5""",
                               (model, *self.REPAIR_TYPES)):
                first_id = row["examples"].split(",")[0]
                out.append(self._ev("service_logs", row["examples"], f"{row['first_seen']}..{row['last_seen']}",
                                    f"{model} repair/diagnostic dispatches: '{row['resolution_summary']}' occurred "
                                    f"{row['n']} times (e.g. {row['examples']}).", f"dispatch_id = '{first_id}'"))
        if "warranty" in r.intents:
            for row in self._q("""SELECT w.status, COUNT(*) n, SUM(w.approved_amount) approved,
                                         GROUP_CONCAT(w.claim_id ORDER BY w.claim_date DESC SEPARATOR ', ' LIMIT 3) examples
                                  FROM warranty_claims w JOIN equipment_units e ON e.equipment_id = w.equipment_id
                                  WHERE e.model = %s GROUP BY w.status ORDER BY n DESC""", (model,)):
                first_id = row["examples"].split(",")[0]
                out.append(self._ev("warranty_claims", row["examples"], "aggregate",
                                    f"{model} warranty claims with status {row['status']}: {row['n']} claims, "
                                    f"${row['approved'] or 0:,.2f} approved (e.g. {row['examples']}).",
                                    f"claim_id = '{first_id}'"))
        return out

    def _suppliers(self, question: str) -> list[Evidence]:
        q = question.lower()
        words = sorted({v for k, v in self.COMPONENT_WORDS.items() if k in q})
        single = "single" in q
        where = " OR ".join(["part_name LIKE %s"] * len(words)) if words and not single else "1=1"
        rows = self._q(f"""SELECT TRIM(part_name) part, COUNT(DISTINCT supplier) n_suppliers,
                                  GROUP_CONCAT(DISTINCT supplier ORDER BY supplier SEPARATOR ', ') suppliers,
                                  GROUP_CONCAT(DISTINCT manufacturer ORDER BY manufacturer SEPARATOR ', ') makers,
                                  GROUP_CONCAT(part_id ORDER BY part_id SEPARATOR ', ') part_ids, SUM(stock_quantity) stock
                           FROM parts_inventory WHERE {where}
                           GROUP BY TRIM(part_name) {'HAVING n_suppliers = 1' if single else ''}
                           ORDER BY n_suppliers, part LIMIT 8""", tuple(f"%{w}%" for w in words) if words and not single else ())
        out = []
        for row in rows:
            risk = "SINGLE-SOURCE RISK" if row["n_suppliers"] == 1 else f"{row['n_suppliers']} suppliers"
            first_id = row["part_ids"].split(",")[0]
            out.append(self._ev("parts_inventory", row["part_ids"], "current inventory",
                                f"Part '{row['part']}' ({row['part_ids']}): supplied by {row['suppliers']} "
                                f"[{risk}]; manufacturers {row['makers']}; {row['stock']} units in stock.",
                                f"part_id = '{first_id}'"))
        return out


# ----------------------------------------------------------------------------- Document (MongoDB)
class DocumentSource:
    PROJ = {"log_id": 1, "timestamp": 1, "equipment_id": 1, "model_code": 1, "status": 1, "fault_code": 1,
            "severity": 1, "telemetry": 1, "flagged_components": 1, "notes": 1}

    def __init__(self) -> None:
        from pymongo import MongoClient
        self.db_name = os.getenv("MONGO_DB", "gigavolt")
        self.coll_name = os.getenv("MONGO_COLLECTION", "telemetry")
        client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=3000)
        self.coll = client[self.db_name][self.coll_name]
        self.coll.estimated_document_count()  # fail fast if MongoDB is down

    def _verify(self, flt: str) -> str:
        return f"sudo docker exec gigavolt-mongo mongosh {self.db_name} --quiet --eval 'db.{self.coll_name}.find({flt})'"

    def _event(self, d: dict) -> Evidence:
        t = d["telemetry"]
        comps = ", ".join(d.get("flagged_components") or []) or "none"
        return Evidence("document", f"MongoDB {self.db_name}.{self.coll_name}", d["log_id"], self.coll_name,
                        d["timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                        f"Telemetry {d['log_id']} at {d['timestamp']:%Y-%m-%d %H:%M} UTC, unit {d['equipment_id']} "
                        f"({d['model_code']}): status {d['status']}, fault {d.get('fault_code') or 'none'}, severity "
                        f"{d['severity']}; {t['temperature_c']} C, {t['voltage_v']} V, {t['current_a']} A, SoC "
                        f"{t['soc_percent']}%, cell delta {t['voltage_delta_mv']} mV; flagged components: {comps}. "
                        f"Notes: {d.get('notes', '')}", self._verify(f'{{_id:"{d["log_id"]}"}}'))

    def _fault_summary(self, match: dict, label: str, limit: int = 6) -> list[Evidence]:
        out = []
        for g in self.coll.aggregate([
            {"$match": {**match, "fault_code": {"$ne": None}}},
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$fault_code", "events": {"$sum": 1}, "units": {"$addToSet": "$equipment_id"},
                        "max_t": {"$max": "$telemetry.temperature_c"}, "avg_t": {"$avg": "$telemetry.temperature_c"},
                        "first": {"$min": "$timestamp"}, "last": {"$max": "$timestamp"},
                        "logs": {"$push": "$log_id"}, "comps": {"$addToSet": "$flagged_components"},
                        "note": {"$first": "$notes"}}},
            {"$sort": {"events": -1}}, {"$limit": limit}]):
            comps = sorted({c for arr in g["comps"] for c in arr})
            ids = ", ".join(g["logs"][:3])
            out.append(Evidence("document", f"MongoDB {self.db_name}.{self.coll_name}", ids, self.coll_name,
                                f"{g['first']:%Y-%m-%d}..{g['last']:%Y-%m-%d}",
                                f"{label}: fault {g['_id']} logged {g['events']} times on units {', '.join(sorted(g['units']))}; "
                                f"temperature avg {g['avg_t']:.1f} C / max {g['max_t']} C; components flagged: "
                                f"{', '.join(comps) or 'none'}; latest note: {g['note']} (e.g. {ids})",
                                self._verify(f'{{fault_code:"{g["_id"]}"}}')))
        return out

    def retrieve(self, r: Route) -> list[Evidence]:
        out: list[Evidence] = []
        hot = "hot" in r.intents
        for serial in r.entities["serial"][:3]:
            total = self.coll.count_documents({"equipment_id": serial})
            if not total:
                out.append(Evidence("document", f"MongoDB {self.db_name}.{self.coll_name}", "no matching document",
                                    self.coll_name, "n/a", f"No telemetry documents exist for unit {serial}.",
                                    self._verify(f'{{equipment_id:"{serial}"}}')))
                continue
            summary = self._fault_summary({"equipment_id": serial}, f"Unit {serial} ({total} telemetry logs)", 3)
            out += summary
            latest = self.coll.find({"equipment_id": serial}, self.PROJ).sort("timestamp", -1).limit(1)
            faults = self.coll.find({"equipment_id": serial, "fault_code": {"$ne": None}}, self.PROJ) \
                              .sort("timestamp", -1).limit(2)
            seen = {e.record_id for e in summary}     # don't repeat a single-event summary as its own record
            for d in list(latest) + list(faults):
                if d["log_id"] not in seen:
                    seen.add(d["log_id"])
                    out.append(self._event(d))
        for code in r.entities["fault"][:2]:
            out += self._fault_summary({"fault_code": code}, f"Fault code {code}", 1)
            out += [self._event(d) for d in self.coll.find({"fault_code": code}, self.PROJ)
                    .sort("timestamp", -1).limit(2)]
        if not r.entities["serial"] and not r.entities["fault"]:
            models = r.entities["model"]
            match = {"model_code": {"$in": models}} if models else {}
            out += self._fault_summary(match, f"{'/'.join(models) or 'Fleet-wide'} telemetry", 5)
            if hot:   # high-temperature questions: also pull the hottest fault events
                out += [self._event(d) for d in self.coll.find({**match, "fault_code": {"$ne": None}}, self.PROJ)
                        .sort("telemetry.temperature_c", -1).limit(2)]
        return out


# ----------------------------------------------------------------------------- Vector (ChromaDB)
class VectorSource:
    def __init__(self) -> None:
        import chromadb
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        client = chromadb.PersistentClient(path=str(HERE / os.getenv("CHROMA_DIR", "chroma_db")))
        self.name = os.getenv("CHROMA_COLLECTION", "gigavolt_docs")
        self.coll = client.get_collection(self.name, embedding_function=DefaultEmbeddingFunction())
        self.coll.query(query_texts=["warm up"], n_results=1)   # load the ONNX model once, up front

    def retrieve(self, r: Route, k: int = 4) -> list[Evidence]:
        hits = []   # (chunk_id, text, metadata, distance, how)
        # 1) exact identifiers (fault codes) -> vector search restricted to chunks that contain the code
        for code in r.entities["fault"][:2]:
            res = self.coll.query(query_texts=[r.question], n_results=2, where_document={"$contains": code})
            hits += [(*row, f"contains {code}") for row in zip(res["ids"][0], res["documents"][0],
                                                               res["metadatas"][0], res["distances"][0])]
        # 2) plain semantic similarity (HNSW, cosine)
        res = self.coll.query(query_texts=[r.question], n_results=k)
        hits += [(*row, "semantic") for row in zip(res["ids"][0], res["documents"][0],
                                                   res["metadatas"][0], res["distances"][0])]
        out, seen = [], set()
        for cid, text, meta, dist, how in hits:
            if cid in seen or len(out) >= k + 1:
                continue
            seen.add(cid)
            out.append(Evidence("vector", f"ChromaDB {self.name}", cid,
                                f"{meta['source_file']} p.{meta['page_number']}", meta.get("indexed_at", "n/a"),
                                f"{meta['source_file']} page {meta['page_number']} ({how}, similarity "
                                f"{1 - dist:.2f}): {text}",
                                f"Open '{meta['source_file']}' page {meta['page_number']}, or "
                                f"collection.get(ids=['{cid}'])"))
        return out


# ----------------------------------------------------------------------------- orchestrator
class HybridRetriever:
    PREFIX = {"sql": "SQL", "document": "MDB", "vector": "VEC"}

    def __init__(self) -> None:
        self.sources, self.init_errors = {}, {}
        for name, cls in (("sql", SqlSource), ("document", DocumentSource), ("vector", VectorSource)):
            try:
                self.sources[name] = cls()
            except Exception as exc:   # one store being down must not kill the pipeline
                self.init_errors[name] = f"{type(exc).__name__}: {exc}"
        self.pool = ThreadPoolExecutor(max_workers=3)

    def _timed(self, name: str, r: Route):
        t0 = time.perf_counter()
        try:
            return name, self.sources[name].retrieve(r), None, (time.perf_counter() - t0) * 1000
        except Exception as exc:
            return name, [], f"{type(exc).__name__}: {exc}", (time.perf_counter() - t0) * 1000

    def retrieve(self, question: str) -> tuple[Route, list[Evidence], dict, dict]:
        t0 = time.perf_counter()
        r = route_question(question)
        timings = {"routing_ms": (time.perf_counter() - t0) * 1000}
        errors = {n: e for n, e in self.init_errors.items() if n in r.sources}
        futures = [self.pool.submit(self._timed, n, r) for n in r.sources if n in self.sources]
        evidence: list[Evidence] = []
        for fut in futures:
            name, items, err, ms = fut.result()
            timings[f"{name}_ms"] = ms
            if err:
                errors[name] = err
            counter = 0
            for ev in items:
                counter += 1
                ev.tag = f"{self.PREFIX[name]}{counter}"
                evidence.append(ev)
        timings["retrieval_ms"] = (time.perf_counter() - t0) * 1000
        return r, evidence, timings, errors


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "What are common failure modes for Model X-7000 batteries in high-temperature environments?"
    hr = HybridRetriever()
    route, ev, tm, errs = hr.retrieve(question)
    print(route.describe())
    for e in ev:
        print(f"[{e.tag}] {e.database} | {e.record_id} | {e.timestamp}\n    {e.text[:200]}")
    print({k: round(v, 1) for k, v in tm.items()}, errs or "")
