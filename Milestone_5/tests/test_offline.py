#!/usr/bin/env python3
"""Offline unit tests: no MariaDB, MongoDB, ChromaDB or API key required.

Run:  python -m unittest tests/test_offline.py -v      (from the Milestone_5 folder)

The telemetry fixture (tests/fixtures/telemetry_sample.jsonl) is a 10-record schema sample:
the first three records mirror the instructor feed; the rest use fixture values and notes.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

M5_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(M5_ROOT))
sys.path.insert(0, str(M5_ROOT / "tests"))

from fake_backends import FakeSQLSource, FakeVector, MiniCollection  # noqa: E402

from gigavolt_rag.config import Settings  # noqa: E402
from gigavolt_rag.display import render_markdown, render_terminal  # noqa: E402
from gigavolt_rag.llm import LLMClient, LLMResponse, extractive_answer  # noqa: E402
from gigavolt_rag.pipeline import RAGPipeline  # noqa: E402
from gigavolt_rag.prompts import build_prompt  # noqa: E402
from gigavolt_rag.provenance import expand_tags, split_statements, validate_answer  # noqa: E402
from gigavolt_rag.retrieval import HybridRetriever  # noqa: E402
from gigavolt_rag.router import plan_retrieval  # noqa: E402
from gigavolt_rag.sources.base import ContextItem  # noqa: E402
from gigavolt_rag.sources.document_source import DocumentSource  # noqa: E402
from gigavolt_rag.sources.sql_source import SQLSource  # noqa: E402
from gigavolt_rag.telemetry import canonical_model, iter_normalized, read_telemetry_file  # noqa: E402

FIXTURE = M5_ROOT / "tests" / "fixtures" / "telemetry_sample.jsonl"
SETTINGS = Settings.from_env(env_file=Path("/nonexistent"))


def load_fixture_docs() -> list[dict]:
    return [payload[0] for kind, payload in iter_normalized(FIXTURE) if kind == "ok"]


def fake_sql_handler(sql: str, params: tuple):
    """Canned MariaDB rows keyed on the SQL text."""
    if "FROM vw_rag_equipment WHERE equipment_id = %s OR serial_number = %s" in sql:
        if params[0] == "EQ-7006":
            return [{"equipment_id": "EQ-7006", "model": "X-7000", "serial_number": "SN-X7000-66607",
                     "install_date": "2021-06-16", "capacity_kw": 750.0, "voltage": "480V",
                     "warranty_expiration": "2022-06-16", "warranty_status": "Expired", "status": "In Service",
                     "location_notes": "Backup unit near loading dock"}]
        return []
    if sql.startswith("SELECT COUNT(*) AS n FROM vw_rag_service_history"):
        return [{"n": 3}]
    if "FROM vw_rag_service_history WHERE equipment_id = %s ORDER BY" in sql:
        return [{"dispatch_id": "DISP-12001", "dispatch_date": "2024-05-01", "technician": "Mike Johnson",
                 "service_type": "Emergency Repair", "hours_spent": 4.5,
                 "resolution_summary": "Replaced burnt fuse block. Test run completed successfully.", "status": "Completed"}]
    if "FROM vw_rag_warranty_claims WHERE equipment_id = %s" in sql:
        return [{"claim_id": "CLM-9001", "dispatch_id": "DISP-12001", "part_id": "PRT-2002", "claim_date": "2024-05-03",
                 "claim_amount": 1031.12, "approved_amount": 903.5, "status": "Approved", "denial_reason": None,
                 "claim_notes": "Claim for replacement part PRT-2002 under equipment warranty."}]
    if "FROM vw_rag_equipment WHERE model = %s GROUP BY status" in sql:
        return [{"status": "In Service", "units": 260, "active_warranty": 120, "first_install": "2020-01-01",
                 "last_install": "2025-06-01", "sample_ids": "EQ-5001,EQ-5002,EQ-5003"}]
    if "GROUP BY resolution_summary" in sql:
        return [{"resolution_summary": "Emergency dispatch for over-temperature alarm. Cleaned heat exchanger.",
                 "dispatches": 190, "repair_dispatches": 60, "first_date": "2021-01-02", "last_date": "2024-12-30",
                 "sample_ids": "DISP-14001,DISP-13999,DISP-13990"}]
    if "FROM vw_rag_warranty_claims WHERE model = %s GROUP BY status" in sql:
        return [{"status": "Approved", "claims": 99, "claimed": 120000.5, "approved": 110000.25,
                 "sample_ids": "CLM-9100,CLM-9101"}]
    if "GROUP BY denial_reason" in sql:
        return [{"denial_reason": "Improper installation by non-certified party", "claims": 12}]
    if sql.startswith("SELECT part_name, category, manufacturer, supplier FROM vw_rag_parts_suppliers"):
        return [{"part_name": "Lithium Cell Module 48V Model-A", "category": "Core Components",
                 "manufacturer": "DisplayTech", "supplier": "Supplier-C"},
                {"part_name": "Solid Lithium Cell Array Model-MAX", "category": "Core Components",
                 "manufacturer": "PowerElec", "supplier": "Supplier-A"}]
    if "FROM vw_rag_parts_suppliers WHERE" in sql:
        return [{"part_id": "PRT-2009", "part_name": "Lithium Cell Module 48V Model-A", "category": "Core Components",
                 "manufacturer": "DisplayTech", "supplier": "Supplier-C", "stock_quantity": 12, "reorder_level": 40,
                 "stock_status": "Below reorder level"},
                {"part_id": "PRT-2038", "part_name": "Solid Lithium Cell Array Model-MAX", "category": "Core Components",
                 "manufacturer": "PowerCable Co", "supplier": "Supplier-A", "stock_quantity": 90, "reorder_level": 20,
                 "stock_status": "OK"}]
    return []


def make_sql() -> SQLSource:
    source = SQLSource(SETTINGS)
    source.query = FakeSQLSource(fake_sql_handler)  # type: ignore[assignment]
    return source


def make_document(docs=None) -> DocumentSource:
    collection = MiniCollection(docs if docs is not None else load_fixture_docs())

    class InMemoryDocumentSource(DocumentSource):
        @property
        def collection(self):  # type: ignore[override]
            return collection

    return InMemoryDocumentSource(SETTINGS)


CHUNKS = [
    {"chunk_id": "GV-INC-2024-104:p1:c1", "document_type": "incident_report", "page_number": 1,
     "source_file": "GV-INC-2024-104 Field Incident Report Contactor Flashover.pdf",
     "text": "Field Incident Investigation Report: SN-X7-0094 Main Contactor Flashover. At 16:04:12 CST on "
             "September 28, 2024, Megapack unit SN-X7-0094 suffered an uncontrolled arc flash within Inverter Bay "
             "INV-01 during a scheduled high-power ramp."},
    {"chunk_id": "GV-ECO-2024-019:p1:c1", "document_type": "engineering_change_order", "page_number": 1,
     "source_file": "GV-ECO-2024-019 Engineering Change Order.pdf",
     "text": "Field telemetry logs indicated recurring instances of ERR_INV_404 (DC Overcurrent Trip) under high "
             "ambient operating temperatures (> 40 °C) on the Megapack X-7000 inverter INV-SCR-9901 failure."},
    {"chunk_id": "GV-SUP-REP-401:p1:c2", "document_type": "supplier_report", "page_number": 1,
     "source_file": "GV-SUP-REP-401 Supply Chain Integration Brief.pdf",
     "text": "Tier-2 Cell Manufacturer: SinoPower Cell Foundry (SUP-SPC-11) supplies Prismacell 280Ah raw lithium "
             "cells (CEL-LFP-280) for the Megapack X-7000 energy storage loop."},
]


class FakeLLM(LLMClient):
    def __init__(self, text: str, ok: bool = True):
        super().__init__(SETTINGS, "anthropic")
        self.text, self._ok = text, ok

    def generate(self, system, user):
        assert "CONTEXT" in user and "grounding" in system.lower()
        if not self._ok:
            return LLMResponse(provider="anthropic", model="fake", error="HTTP 401: invalid x-api-key", elapsed_ms=5)
        return LLMResponse(text=self.text, provider="anthropic", model="fake", elapsed_ms=900.0)


def make_pipeline(llm) -> RAGPipeline:
    retriever = HybridRetriever(SETTINGS, sql=make_sql(), vector=FakeVector(CHUNKS), document=make_document())
    return RAGPipeline(SETTINGS, retriever=retriever, llm=llm)


# =============================================================================== tests
class TelemetryParsingTests(unittest.TestCase):
    def test_ndjson_and_array_formats(self):
        parsed = read_telemetry_file(FIXTURE)
        self.assertEqual(parsed.format, "ndjson")
        self.assertEqual(len(parsed.records), 10)
        with tempfile.TemporaryDirectory() as tmp:
            array_file = Path(tmp) / "array.json"
            array_file.write_text(json.dumps([r for _, r in parsed.records]), encoding="utf-8")
            self.assertEqual(read_telemetry_file(array_file).format, "json_array")
            wrapped = Path(tmp) / "wrapped.json"
            wrapped.write_text(json.dumps({"logs": [r for _, r in parsed.records]}, indent=2), encoding="utf-8")
            self.assertEqual(len(read_telemetry_file(wrapped).records), 10)

    def test_normalization_and_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            good = FIXTURE.read_text(encoding="utf-8").splitlines()[2]
            path.write_text(good + "\n{not json}\n" + json.dumps({"log_id": "TLM-1", "equipment_id": "SN-1"}) + "\n",
                            encoding="utf-8")
            results = list(iter_normalized(path, ingested_at=datetime(2026, 11, 7, tzinfo=timezone.utc)))
        oks = [p for k, p in results if k == "ok"]
        rejects = [p for k, p in results if k == "reject"]
        self.assertEqual(len(oks), 1)
        self.assertEqual(len(rejects), 2)
        doc = oks[0][0]
        self.assertEqual(doc["_id"], "TLM-90003")
        self.assertEqual(doc["model_code"], "X-7000")
        self.assertEqual(doc["fault_family"], "PRE")
        self.assertTrue(doc["is_fault"])
        self.assertEqual(doc["severity_rank"], 3)
        self.assertIsInstance(doc["timestamp"], datetime)
        self.assertEqual(doc["provenance"]["source_line"], 1)

    def test_canonical_model(self):
        self.assertEqual(canonical_model("Gigavolt Megapack X-7000"), "X-7000")
        self.assertEqual(canonical_model("PowerStation G-3500"), "G-3500")
        self.assertEqual(canonical_model("C-1200"), "C-1200")


class RouterTests(unittest.TestCase):
    def test_rubric_query_failure_modes(self):
        plan = plan_retrieval("What are common failure modes for Model X-7000 batteries in high-temperature environments?")
        self.assertIn("failure_analysis", plan.intents)
        self.assertEqual(plan.entities.models, ["X-7000"])
        self.assertTrue(plan.entities.high_temperature)
        self.assertTrue(plan.use_sql and plan.use_document and plan.use_vector)
        self.assertIn("model_fault_summary", plan.mongo_tasks)
        self.assertIn("model_service_summary", plan.sql_tasks)

    def test_rubric_query_suppliers(self):
        plan = plan_retrieval("Which suppliers provide lithium cells for our commercial energy storage units?")
        self.assertIn("supply_chain", plan.intents)
        self.assertIn("parts_suppliers", plan.sql_tasks)
        self.assertIn("component_events", plan.mongo_tasks)
        self.assertIn("supplier_report", plan.vector_doc_types)

    def test_rubric_query_unit_history(self):
        plan = plan_retrieval("Show me service history for equipment unit SN-4829 and related warranty claims")
        self.assertEqual(plan.entities.serial_numbers, ["SN-4829"])
        self.assertIn("unit_lookup", plan.sql_tasks)
        self.assertIn("unit_events", plan.mongo_tasks)

    def test_procedure_question_routes_to_vector_only(self):
        plan = plan_retrieval("What are the emergency response stages for confirmed thermal runaway?")
        self.assertFalse(plan.use_sql)
        self.assertFalse(plan.use_document)
        self.assertTrue(plan.use_vector)

    def test_entity_extraction(self):
        plan = plan_retrieval("Which events flagged CEL-LFP-280 or ERR_BMS_708 on SN-C12-0055 and EQ-5805?")
        e = plan.entities
        self.assertEqual(e.component_codes, ["CEL-LFP-280"])
        self.assertEqual(e.fault_codes, ["ERR_BMS_708"])
        self.assertEqual(e.serial_numbers, ["SN-C12-0055"])
        self.assertEqual(e.equipment_ids, ["EQ-5805"])


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            ContextItem("sql", "MariaDB", "vw_rag_equipment", ["lookup:SN-4829"], "absence",
                        "NO row in equipment_units has serial SN-4829.", kind="absence", citation_id="S1"),
            ContextItem("document", "MongoDB", "bms_telemetry", ["TLM-90028", "TLM-90038"], "summary",
                        "Telemetry summary for SN-4829: 18 BMS events. Max temperature 31.0 °C.", kind="aggregate",
                        citation_id="D1"),
        ]

    def test_tags_and_statements(self):
        self.assertEqual(expand_tags("S1, S2; D3"), ["S1", "S2", "D3"])
        self.assertEqual(expand_tags("S1, 2"), ["S1", "S2"])
        statements = split_statements("Answer: No ERP history exists. [S1]\nKey findings:\n- 18 events [D1].\nGaps: None.")
        self.assertEqual(statements, ["No ERP history exists. [S1]", "18 events [D1]."])

    def test_full_coverage_passes(self):
        answer = ("Answer: The ERP has no record of SN-4829 [S1].\nKey findings:\n"
                  "- Telemetry shows 18 events with a max temperature of 31.0 °C [D1].\nGaps: None.")
        report = validate_answer(answer, self.items)
        self.assertEqual(report.coverage_pct, 100.0)
        self.assertTrue(report.passed)
        self.assertEqual(report.numeric_grounding_pct, 100.0)
        self.assertEqual(set(report.sources), {"S1", "D1"})

    def test_detects_uncited_invalid_and_unsupported_numbers(self):
        answer = "Answer: SN-4829 had 25 events [D1].\nKey findings:\n- It failed twice [V9].\n- It is fine."
        report = validate_answer(answer, self.items)
        self.assertFalse(report.passed)
        self.assertEqual(report.invalid_citations, ["V9"])
        self.assertEqual(len(report.uncited), 2)
        self.assertEqual(report.unsupported_numbers[0][1], "25")

    def test_extractive_answer_is_fully_cited(self):
        report = validate_answer(extractive_answer("q", self.items), self.items)
        self.assertEqual(report.coverage_pct, 100.0)


class SourceTests(unittest.TestCase):
    def test_sql_absence_and_records(self):
        sql = make_sql()
        absent = sql.unit_lookup("SN-4829")
        self.assertEqual(len(absent), 1)
        self.assertEqual(absent[0].kind, "absence")
        found = sql.unit_lookup("EQ-7006")
        kinds = [i.collection for i in found]
        self.assertEqual(kinds, ["vw_rag_equipment", "vw_rag_service_history", "vw_rag_warranty_claims"])
        self.assertIn("Expired", found[0].text)
        self.assertEqual(found[2].record_ids, ["CLM-9001"])

    def test_sql_parts_grouped_by_supplier(self):
        plan = plan_retrieval("Which suppliers provide lithium cells for our commercial energy storage units?")
        items = make_sql().parts_suppliers(plan)
        self.assertEqual([i.title for i in items], ["Supplier-C parts", "Supplier-A parts"])
        self.assertIn("Below reorder level", items[0].text)
        self.assertEqual(items[0].verify["engine"], "mariadb")

    def test_document_unit_events_precursor_window(self):
        items = make_document().unit_events("SN-X7-0094")
        titles = [i.title for i in items]
        self.assertIn("SN-X7-0094 precursor window", titles)
        window = next(i for i in items if i.title.endswith("precursor window"))
        self.assertEqual(window.record_ids, ["TLM-90001", "TLM-90002", "TLM-90003", "TLM-90004"])
        self.assertIn("ERR_PRE_003", window.text)

    def test_document_model_summary_and_absence(self):
        source = make_document()
        items = source.model_fault_summary("X-7000", 45.0)
        self.assertIn("X-7000 telemetry overview", items[0].title)
        codes = [i.title for i in items[1:]]
        self.assertTrue(any("ERR_INV_404" in t for t in codes))
        absent = source.model_fault_summary("X-9000", 45.0)
        self.assertEqual(absent[0].kind, "absence")

    def test_document_component_events(self):
        items = make_document().component_events(["CEL-LFP-280"], ["CEL-"])
        components = {i.title for i in items}
        self.assertIn("Component CEL-LFP-280 in telemetry", components)
        self.assertIn("Component CEL-NMC-065 in telemetry", components)


class PipelineTests(unittest.TestCase):
    def test_end_to_end_with_llm(self):
        answer = ("Answer: SN-X7-0094 hit a pre-charge fault then a critical inverter fault [D3][V1].\n"
                  "Key findings:\n- The sequence ended in ERR_INV_404 at 85.0 °C [D3].\n"
                  "- The incident report describes an arc flash in Inverter Bay INV-01 [V1].\n"
                  "Gaps: The ERP holds no record of this serial number [S1].")
        pipeline = make_pipeline(FakeLLM(answer))
        result = pipeline.answer("What happened to unit SN-X7-0094 on September 28, 2024, and what was the root cause?")
        self.assertEqual(result.mode, "llm")
        self.assertTrue(result.retrieval.sources["document"].items)
        self.assertTrue(result.retrieval.sources["vector"].items)
        self.assertEqual(result.retrieval.sources["sql"].items[0].kind, "absence")
        self.assertIn("total", result.timings_ms)
        render_terminal(result)
        render_markdown(result)
        json.dumps(result.to_dict())

    def test_llm_failure_falls_back_to_extractive(self):
        pipeline = make_pipeline(FakeLLM("", ok=False))
        result = pipeline.answer("Show me service history for equipment unit EQ-7006 and related warranty claims")
        self.assertEqual(result.mode, "extractive_fallback")
        self.assertEqual(result.provenance.coverage_pct, 100.0)
        self.assertTrue(any("LLM unavailable" in w for w in result.warnings))

    def test_source_outage_degrades_gracefully(self):
        broken = make_sql()
        broken.query = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("MariaDB down"))  # type: ignore
        retriever = HybridRetriever(SETTINGS, sql=broken, vector=FakeVector(CHUNKS), document=make_document())
        pipeline = RAGPipeline(SETTINGS, retriever=retriever, llm=LLMClient(SETTINGS, "extractive"))
        result = pipeline.answer("What are common failure modes for Model X-7000 batteries in high-temperature environments?")
        self.assertTrue(result.retrieval.sources["sql"].error)
        self.assertTrue(result.retrieval.sources["document"].items)
        self.assertTrue(any("sql source error" in w for w in result.warnings))

    def test_prompt_contains_context_and_rules(self):
        retriever = HybridRetriever(SETTINGS, sql=make_sql(), vector=FakeVector(CHUNKS), document=make_document())
        retrieval = retriever.retrieve("Which suppliers provide lithium cells for our commercial energy storage units?")
        system, user = build_prompt(retrieval)
        self.assertIn("MUST end with one or more citation tags", system)
        self.assertIn("[S1]", user)
        self.assertIn("Graph database: not part of this milestone", user)

    def test_empty_and_out_of_scope(self):
        pipeline = make_pipeline(FakeLLM("unused"))
        self.assertEqual(pipeline.answer("  ").mode, "invalid_question")
        result = pipeline.answer("What is the capital of France?")
        self.assertEqual(result.mode, "no_context")


if __name__ == "__main__":
    unittest.main(verbosity=2)
