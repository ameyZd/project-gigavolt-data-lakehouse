"""MariaDB retrieval: structured ERP facts through least-privilege views.

Every query is parameterized, reads only the ``vw_rag_*`` views from
``sql/01_rag_views_and_indexes.sql``, and records the exact SQL + parameters in
``ContextItem.verify`` so ``scripts/verify_citation.py`` can re-run it.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Any

from ..config import Settings
from ..router import RetrievalPlan
from .base import ContextItem, SourceResult, jsonable

DB_LABEL = "MariaDB gigavolt_silver"
PART_STOPWORDS = {
    "model", "max", "pro", "unit", "units", "our", "the", "for", "and", "which", "who", "what", "provide",
    "provides", "supplier", "suppliers", "commercial", "energy", "storage", "system", "systems", "part", "parts",
    "a", "b", "c", "with", "from", "that", "are", "is", "of", "in", "to", "gigavolt", "inc", "co",
}
SEARCHABLE = "LOWER(CONCAT_WS(' ', part_name, category, manufacturer, supplier))"


def _fmt(value: Any) -> str:
    value = jsonable(value)
    if value is None or value == "":
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


class SQLSource:
    source_type = "sql"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._conn = None
        self._lock = threading.Lock()
        self._part_vocabulary: set[str] | None = None

    # ------------------------------------------------------------------ connection
    def _connect(self):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as error:  # pragma: no cover - environment specific
            raise RuntimeError("PyMySQL missing: pip install -r requirements.txt") from error
        kwargs: dict[str, Any] = dict(
            user=self.settings.mariadb_user,
            password=self.settings.mariadb_password,
            database=self.settings.mariadb_database,
            cursorclass=DictCursor,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=3,
            read_timeout=8,
        )
        if self.settings.mariadb_socket:
            kwargs["unix_socket"] = self.settings.mariadb_socket
        else:
            kwargs["host"] = self.settings.mariadb_host
            kwargs["port"] = self.settings.mariadb_port
        return pymysql.connect(**kwargs)

    def connection(self):
        if self._conn is None:
            self._conn = self._connect()
        else:
            try:
                self._conn.ping(reconnect=True)
            except Exception:
                self._conn = self._connect()
        return self._conn

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            with self.connection().cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())

    def warmup(self) -> None:
        self.query("SELECT 1 AS ok")
        self._load_part_vocabulary()

    def health(self) -> dict[str, Any]:
        rows = self.query(
            "SELECT 'vw_rag_equipment' AS view_name, COUNT(*) AS n FROM vw_rag_equipment "
            "UNION ALL SELECT 'vw_rag_service_history', COUNT(*) FROM vw_rag_service_history "
            "UNION ALL SELECT 'vw_rag_warranty_claims', COUNT(*) FROM vw_rag_warranty_claims "
            "UNION ALL SELECT 'vw_rag_parts_suppliers', COUNT(*) FROM vw_rag_parts_suppliers"
        )
        status = {row["view_name"]: row["n"] for row in rows}
        try:
            version = self.query("SELECT VERSION() AS v")
            status["server_version"] = version[0]["v"] if version else "n/a"
            # Single-instance MariaDB is the default; if Galera happens to be running, report its size too.
            cluster = self.query("SHOW STATUS LIKE 'wsrep_cluster_size'")
            status["topology"] = (f"Galera cluster, {cluster[0]['Value']} node(s)"
                                  if cluster and cluster[0].get("Value") not in (None, "", "0")
                                  else "single MariaDB instance")
        except Exception as error:  # status visibility can vary by privilege
            status["topology"] = f"unavailable ({error.__class__.__name__})"
        return status

    # ------------------------------------------------------------------ helpers
    def _item(self, collection: str, record_ids: list[str], title: str, text: str, sql: str, params: tuple,
              kind: str = "record", timestamp: str | None = None) -> ContextItem:
        return ContextItem(
            source_type="sql",
            database=DB_LABEL,
            collection=collection,
            record_ids=[str(r) for r in record_ids if r],
            title=title,
            text=text,
            kind=kind,
            timestamp=timestamp,
            verify={"engine": "mariadb", "sql": " ".join(sql.split()), "params": list(params)},
        )

    def _load_part_vocabulary(self) -> set[str]:
        if self._part_vocabulary is None:
            rows = self.query("SELECT part_name, category, manufacturer, supplier FROM vw_rag_parts_suppliers")
            vocab: set[str] = set()
            for row in rows:
                for value in row.values():
                    vocab.update(w for w in re.findall(r"[a-z0-9]+", str(value or "").lower()) if len(w) > 2)
            self._part_vocabulary = vocab - PART_STOPWORDS
        return self._part_vocabulary

    # ------------------------------------------------------------------ tasks
    def unit_lookup(self, unit_id: str) -> list[ContextItem]:
        limit = self.settings.sql_row_limit
        sql_eq = ("SELECT equipment_id, model, serial_number, install_date, capacity_kw, voltage, warranty_expiration, "
                  "warranty_status, status, location_notes FROM vw_rag_equipment "
                  "WHERE equipment_id = %s OR serial_number = %s LIMIT 1")
        rows = self.query(sql_eq, (unit_id, unit_id))
        if not rows:
            return [self._item(
                "vw_rag_equipment", [f"lookup:{unit_id}"], f"No ERP equipment record for {unit_id}",
                f"ERP lookup result: NO row in equipment_units has equipment_id = '{unit_id}' or serial_number = "
                f"'{unit_id}'. Therefore the ERP holds no service history and no warranty claims linked to {unit_id}. "
                "(Exact match only; similar-looking IDs were deliberately not substituted.)",
                sql_eq, (unit_id, unit_id), kind="absence")]
        eq = rows[0]
        equipment_id = eq["equipment_id"]
        items = [self._item(
            "vw_rag_equipment", [equipment_id], f"Equipment {equipment_id} ({eq['model']})",
            f"Equipment {equipment_id}: model {eq['model']}, serial {eq['serial_number']}, installed {_fmt(eq['install_date'])}, "
            f"capacity {_fmt(eq['capacity_kw'])} kW, voltage {_fmt(eq['voltage'])}, operating status {_fmt(eq['status'])}, "
            f"location '{_fmt(eq['location_notes'])}'. Warranty expires {_fmt(eq['warranty_expiration'])} "
            f"(warranty status today: {eq['warranty_status']}).",
            sql_eq, (unit_id, unit_id), timestamp=_fmt(eq["install_date"]))]

        sql_count = "SELECT COUNT(*) AS n FROM vw_rag_service_history WHERE equipment_id = %s"
        total = self.query(sql_count, (equipment_id,))[0]["n"]
        sql_hist = ("SELECT dispatch_id, dispatch_date, technician, service_type, hours_spent, resolution_summary, status "
                    "FROM vw_rag_service_history WHERE equipment_id = %s "
                    "ORDER BY dispatch_date DESC, dispatch_id DESC LIMIT %s")
        history = self.query(sql_hist, (equipment_id, limit))
        if not history:
            items.append(self._item("vw_rag_service_history", [f"lookup:{equipment_id}"],
                                    f"No service history for {equipment_id}",
                                    f"ERP lookup result: NO service_logs (dispatch) rows exist for {equipment_id}.",
                                    sql_hist, (equipment_id, limit), kind="absence"))
        for row in history:
            items.append(self._item(
                "vw_rag_service_history", [row["dispatch_id"]], f"Dispatch {row['dispatch_id']}",
                f"Dispatch {row['dispatch_id']} on {_fmt(row['dispatch_date'])} for {equipment_id}: {row['service_type']} by "
                f"technician {_fmt(row['technician'])}, {_fmt(row['hours_spent'])} h, status {_fmt(row['status'])}. "
                f"Resolution: {_fmt(row['resolution_summary'])}"
                + (f" [Unit has {total} dispatches in total; the {len(history)} most recent are listed.]"
                   if row is history[0] and total > len(history) else ""),
                "SELECT * FROM vw_rag_service_history WHERE dispatch_id = %s", (row["dispatch_id"],),
                timestamp=_fmt(row["dispatch_date"])))

        sql_claims = ("SELECT claim_id, dispatch_id, part_id, claim_date, claim_amount, approved_amount, status, "
                      "denial_reason, claim_notes FROM vw_rag_warranty_claims WHERE equipment_id = %s "
                      "ORDER BY claim_date DESC, claim_id DESC LIMIT %s")
        claims = self.query(sql_claims, (equipment_id, limit))
        if not claims:
            items.append(self._item("vw_rag_warranty_claims", [f"lookup:{equipment_id}"],
                                    f"No warranty claims for {equipment_id}",
                                    f"ERP lookup result: NO warranty_claims rows exist for {equipment_id}.",
                                    sql_claims, (equipment_id, limit), kind="absence"))
        for row in claims:
            denial = _fmt(row["denial_reason"])
            items.append(self._item(
                "vw_rag_warranty_claims", [row["claim_id"]], f"Warranty claim {row['claim_id']}",
                f"Warranty claim {row['claim_id']} filed {_fmt(row['claim_date'])} for {equipment_id}: status "
                f"{_fmt(row['status'])}, claimed ${_fmt(row['claim_amount'])}, approved ${_fmt(row['approved_amount'])}, "
                f"linked dispatch {_fmt(row['dispatch_id'])}, part {_fmt(row['part_id'])}"
                + (f", denial reason '{denial}'" if denial not in ("n/a", "N/A") else "")
                + f". Notes: {_fmt(row['claim_notes'])}",
                "SELECT * FROM vw_rag_warranty_claims WHERE claim_id = %s", (row["claim_id"],),
                timestamp=_fmt(row["claim_date"])))
        return items

    def record_lookup(self, plan: RetrievalPlan) -> list[ContextItem]:
        items: list[ContextItem] = []
        for dispatch_id in plan.entities.dispatch_ids:
            sql = "SELECT * FROM vw_rag_service_history WHERE dispatch_id = %s"
            for row in self.query(sql, (dispatch_id,)) or [None]:
                if row is None:
                    items.append(self._item("vw_rag_service_history", [f"lookup:{dispatch_id}"], f"No dispatch {dispatch_id}",
                                            f"ERP lookup result: dispatch {dispatch_id} does not exist.", sql, (dispatch_id,),
                                            kind="absence"))
                    continue
                items.append(self._item("vw_rag_service_history", [dispatch_id], f"Dispatch {dispatch_id}",
                                        "; ".join(f"{k}={_fmt(v)}" for k, v in row.items()), sql, (dispatch_id,),
                                        timestamp=_fmt(row.get("dispatch_date"))))
        for claim_id in plan.entities.claim_ids:
            sql = "SELECT * FROM vw_rag_warranty_claims WHERE claim_id = %s"
            for row in self.query(sql, (claim_id,)) or [None]:
                if row is None:
                    items.append(self._item("vw_rag_warranty_claims", [f"lookup:{claim_id}"], f"No claim {claim_id}",
                                            f"ERP lookup result: warranty claim {claim_id} does not exist.", sql, (claim_id,),
                                            kind="absence"))
                    continue
                items.append(self._item("vw_rag_warranty_claims", [claim_id], f"Claim {claim_id}",
                                        "; ".join(f"{k}={_fmt(v)}" for k, v in row.items()), sql, (claim_id,),
                                        timestamp=_fmt(row.get("claim_date"))))
        return items

    def model_fleet_summary(self, model: str) -> list[ContextItem]:
        sql = ("SELECT status, COUNT(*) AS units, SUM(warranty_status = 'Active') AS active_warranty, "
               "MIN(install_date) AS first_install, MAX(install_date) AS last_install, "
               "SUBSTRING_INDEX(GROUP_CONCAT(equipment_id ORDER BY equipment_id SEPARATOR ','), ',', 3) AS sample_ids "
               "FROM vw_rag_equipment WHERE model = %s GROUP BY status ORDER BY units DESC")
        rows = self.query(sql, (model,))
        if not rows:
            return [self._item("vw_rag_equipment", [f"lookup:model={model}"], f"No ERP units of model {model}",
                               f"ERP lookup result: NO equipment_units rows have model = '{model}'. The ERP fleet does not "
                               f"track this model, so ERP service and warranty statistics are unavailable for it.",
                               sql, (model,), kind="absence")]
        total = sum(int(r["units"]) for r in rows)
        lines = [f"{r['status']}: {r['units']} units ({int(r['active_warranty'] or 0)} with active warranty)" for r in rows]
        samples = [s for r in rows for s in str(r["sample_ids"] or "").split(",") if s][:6]
        first = min(str(r["first_install"]) for r in rows if r["first_install"]) if any(r["first_install"] for r in rows) else None
        last = max(str(r["last_install"]) for r in rows if r["last_install"]) if any(r["last_install"] for r in rows) else None
        return [self._item("vw_rag_equipment", samples, f"ERP fleet summary for {model}",
                           f"ERP fleet for model {model}: {total} units in total. By operating status: " + "; ".join(lines)
                           + f". Install dates range {first} to {last}. Example equipment IDs: {', '.join(samples)}.",
                           sql, (model,), kind="aggregate", timestamp=f"{first}..{last}")]

    def model_service_summary(self, model: str) -> list[ContextItem]:
        sql = ("SELECT resolution_summary, COUNT(*) AS dispatches, "
               "SUM(service_type IN ('Emergency Repair', 'Corrective Maintenance')) AS repair_dispatches, "
               "MIN(dispatch_date) AS first_date, MAX(dispatch_date) AS last_date, "
               "SUBSTRING_INDEX(GROUP_CONCAT(dispatch_id ORDER BY dispatch_date DESC SEPARATOR ','), ',', 3) AS sample_ids "
               "FROM vw_rag_service_history WHERE model = %s "
               "GROUP BY resolution_summary ORDER BY dispatches DESC LIMIT 6")
        rows = self.query(sql, (model,))
        if not rows:
            return []
        total = sum(int(r["dispatches"]) for r in rows)
        items = []
        for row in rows:
            samples = [s for s in str(row["sample_ids"] or "").split(",") if s]
            share = 100.0 * int(row["dispatches"]) / total if total else 0.0
            items.append(self._item(
                "vw_rag_service_history", samples, f"{model} service outcome",
                f"Field-service records for model {model}: resolution '{_fmt(row['resolution_summary'])}' appears in "
                f"{row['dispatches']} of {total} dispatches ({share:.1f}%), of which {int(row['repair_dispatches'] or 0)} were "
                f"Emergency Repair or Corrective Maintenance visits, between {_fmt(row['first_date'])} and "
                f"{_fmt(row['last_date'])}. Example dispatches: {', '.join(samples)}.",
                sql, (model,), kind="aggregate", timestamp=f"{_fmt(row['first_date'])}..{_fmt(row['last_date'])}"))
        return items

    def model_warranty_summary(self, model: str) -> list[ContextItem]:
        sql = ("SELECT status, COUNT(*) AS claims, ROUND(SUM(claim_amount), 2) AS claimed, "
               "ROUND(SUM(approved_amount), 2) AS approved, "
               "SUBSTRING_INDEX(GROUP_CONCAT(claim_id ORDER BY claim_date DESC SEPARATOR ','), ',', 2) AS sample_ids "
               "FROM vw_rag_warranty_claims WHERE model = %s GROUP BY status ORDER BY claims DESC")
        rows = self.query(sql, (model,))
        if not rows:
            return []
        sql_denial = ("SELECT denial_reason, COUNT(*) AS claims FROM vw_rag_warranty_claims WHERE model = %s "
                      "AND denial_reason IS NOT NULL AND TRIM(denial_reason) NOT IN ('', 'N/A') "
                      "GROUP BY denial_reason ORDER BY claims DESC LIMIT 4")
        denials = self.query(sql_denial, (model,))
        samples = [s for r in rows for s in str(r["sample_ids"] or "").split(",") if s][:8]
        text = (f"Warranty claims for model {model}: "
                + "; ".join(f"{r['status']}: {r['claims']} claims (claimed ${_fmt(r['claimed'])}, approved ${_fmt(r['approved'])})"
                            for r in rows) + ".")
        if denials:
            text += " Most common denial reasons: " + "; ".join(f"'{d['denial_reason']}' ({d['claims']})" for d in denials) + "."
        text += f" Example claims: {', '.join(samples)}."
        return [self._item("vw_rag_warranty_claims", samples, f"{model} warranty summary", text, sql, (model,),
                           kind="aggregate")]

    def warranty_overview(self) -> list[ContextItem]:
        sql = ("SELECT model, status, COUNT(*) AS claims FROM vw_rag_warranty_claims "
               "GROUP BY model, status ORDER BY model, claims DESC")
        rows = self.query(sql)
        by_model: dict[str, list[str]] = OrderedDict()
        for row in rows:
            by_model.setdefault(row["model"], []).append(f"{row['status']} {row['claims']}")
        text = "Warranty claims by model and status: " + "; ".join(f"{m}: {', '.join(v)}" for m, v in by_model.items()) + "."
        return [self._item("vw_rag_warranty_claims", [f"agg:{m}" for m in by_model], "Warranty overview", text, sql, (),
                           kind="aggregate")]

    def parts_suppliers(self, plan: RetrievalPlan) -> list[ContextItem]:
        entities = plan.entities
        base = ("SELECT part_id, part_name, category, manufacturer, supplier, stock_quantity, reorder_level, stock_status "
                "FROM vw_rag_parts_suppliers")
        if entities.part_ids:
            placeholders = ", ".join(["%s"] * len(entities.part_ids))
            sql, params = f"{base} WHERE part_id IN ({placeholders}) ORDER BY part_id", tuple(entities.part_ids)
            matched_terms = entities.part_ids
        else:
            vocab = self._load_part_vocabulary()
            words = re.findall(r"[a-z0-9]+", plan.question.lower())
            terms = []
            for word in words:
                for candidate in (word, word[:-1] if word.endswith("s") and len(word) > 3 else word):
                    if candidate in vocab and candidate not in terms:
                        terms.append(candidate)
                        break
            if not terms:
                sql = ("SELECT supplier, COUNT(*) AS parts, GROUP_CONCAT(DISTINCT category ORDER BY category SEPARATOR ', ') "
                       "AS categories, SUM(stock_status = 'Below reorder level') AS below_reorder FROM vw_rag_parts_suppliers "
                       "GROUP BY supplier ORDER BY supplier")
                rows = self.query(sql)
                text = "ERP suppliers in parts_inventory: " + "; ".join(
                    f"{r['supplier']} supplies {r['parts']} parts ({r['categories']}; {int(r['below_reorder'] or 0)} below reorder level)"
                    for r in rows) + "."
                return [self._item("vw_rag_parts_suppliers", [f"agg:{r['supplier']}" for r in rows], "Supplier overview",
                                   text, sql, (), kind="aggregate")]
            clauses = " AND ".join([f"{SEARCHABLE} LIKE %s"] * len(terms))
            sql, params = f"{base} WHERE {clauses} ORDER BY supplier, part_id LIMIT 40", tuple(f"%{t}%" for t in terms)
            matched_terms = terms
        rows = self.query(sql, params)
        if not rows and not entities.part_ids and len(matched_terms) > 1:  # relax AND -> OR
            clauses = " OR ".join([f"{SEARCHABLE} LIKE %s"] * len(matched_terms))
            sql = f"{base} WHERE {clauses} ORDER BY supplier, part_id LIMIT 40"
            rows = self.query(sql, params)
        if not rows:
            return [self._item("vw_rag_parts_suppliers", [f"lookup:{'+'.join(matched_terms)}"], "No matching parts",
                               f"ERP lookup result: NO parts_inventory rows match {matched_terms}.", sql, params,
                               kind="absence")]
        by_supplier: dict[str, list[dict[str, Any]]] = OrderedDict()
        for row in rows:
            by_supplier.setdefault(str(row["supplier"]), []).append(row)
        items = []
        for supplier, parts in by_supplier.items():
            lines = [f"{p['part_id']} {p['part_name'].strip()} (manufacturer {p['manufacturer']}, category {p['category']}, "
                     f"stock {p['stock_quantity']} vs reorder level {p['reorder_level']}: {p['stock_status']})" for p in parts]
            items.append(self._item(
                "vw_rag_parts_suppliers", [p["part_id"] for p in parts], f"{supplier} parts",
                f"ERP parts_inventory: supplier {supplier} supplies {len(parts)} part(s) matching "
                f"'{' + '.join(matched_terms)}': " + "; ".join(lines) + ".",
                f"{base} WHERE supplier = %s AND part_id IN ({', '.join(['%s'] * len(parts))})",
                (supplier, *[p["part_id"] for p in parts]), kind="aggregate"))
        return items

    # ------------------------------------------------------------------ entry point
    def retrieve(self, plan: RetrievalPlan) -> SourceResult:
        started = time.perf_counter()
        result = SourceResult(source_type="sql")
        task_ms: dict[str, float] = {}
        errors: list[str] = []
        for task in plan.sql_tasks:
            task_started = time.perf_counter()
            try:
                if task == "unit_lookup":
                    for unit_id in plan.entities.unit_ids[:3]:
                        result.items.extend(self.unit_lookup(unit_id))
                elif task == "record_lookup":
                    result.items.extend(self.record_lookup(plan))
                elif task == "model_fleet_summary":
                    for model in plan.entities.models[:2]:
                        result.items.extend(self.model_fleet_summary(model))
                elif task == "model_service_summary":
                    for model in plan.entities.models[:2]:
                        result.items.extend(self.model_service_summary(model))
                elif task == "model_warranty_summary":
                    for model in plan.entities.models[:2]:
                        result.items.extend(self.model_warranty_summary(model))
                elif task == "warranty_overview":
                    result.items.extend(self.warranty_overview())
                elif task == "parts_suppliers":
                    result.items.extend(self.parts_suppliers(plan))
            except Exception as error:  # keep other tasks/sources alive
                errors.append(f"{task}: {error.__class__.__name__}: {error}")
            task_ms[task] = round((time.perf_counter() - task_started) * 1000, 3)
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        result.details = {"task_ms": task_ms, "tasks": plan.sql_tasks}
        if errors:
            result.details["errors"] = errors
            if not result.items:
                result.error = "; ".join(errors)
        return result
