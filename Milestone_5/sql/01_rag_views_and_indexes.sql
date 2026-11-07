-- Project Gigavolt | Milestone 5 | Least-privilege SQL surface for the RAG pipeline
-- Run on the EC2 instance (single MariaDB server):  sudo mariadb < sql/01_rag_views_and_indexes.sql
--
-- Why views?  Milestone 2 established that AI/support consumers never read base tables.
-- The RAG retriever gets SELECT on these four views only. Customer names, contacts,
-- addresses and part cost/price columns are not exposed to the LLM.

USE gigavolt_silver;

-- ---------------------------------------------------------------------------
-- Supporting indexes for the retriever's metadata filters (model / serial lookups).
-- Foreign-key indexes on service_logs.equipment_id and warranty_claims.equipment_id
-- already exist from Milestone 1 (InnoDB creates them automatically).
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_equipment_model         ON equipment_units (model);
CREATE INDEX IF NOT EXISTS ix_equipment_serial        ON equipment_units (serial_number);
CREATE INDEX IF NOT EXISTS ix_service_equipment_date  ON service_logs (equipment_id, dispatch_date);
CREATE INDEX IF NOT EXISTS ix_claims_equipment_date   ON warranty_claims (equipment_id, claim_date);

-- Equipment metadata + derived warranty status (the "SQL metadata filter" source).
CREATE OR REPLACE SQL SECURITY DEFINER VIEW vw_rag_equipment AS
SELECT
    eu.equipment_id,
    eu.model,
    eu.serial_number,
    eu.install_date,
    eu.capacity_kw,
    eu.voltage,
    eu.warranty_expiration,
    CASE
        WHEN eu.warranty_expiration IS NULL THEN 'Unknown'
        WHEN eu.warranty_expiration >= CURRENT_DATE THEN 'Active'
        ELSE 'Expired'
    END AS warranty_status,
    eu.status,
    eu.location_notes
FROM equipment_units AS eu;

-- Service history (same idea as Milestone 2's vw_service_rag_safe, plus hours spent).
CREATE OR REPLACE SQL SECURITY DEFINER VIEW vw_rag_service_history AS
SELECT
    sl.dispatch_id,
    sl.equipment_id,
    eu.model,
    eu.serial_number,
    sl.dispatch_date,
    sl.technician,
    sl.service_type,
    sl.hours_spent,
    sl.resolution_summary,
    sl.status
FROM service_logs AS sl
JOIN equipment_units AS eu ON eu.equipment_id = sl.equipment_id;

-- Warranty claims joined to the equipment model so they can be filtered by model.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW vw_rag_warranty_claims AS
SELECT
    wc.claim_id,
    wc.equipment_id,
    eu.model,
    eu.serial_number,
    wc.dispatch_id,
    wc.part_id,
    wc.claim_date,
    wc.claim_amount,
    wc.approved_amount,
    wc.status,
    wc.denial_reason,
    wc.claim_notes
FROM warranty_claims AS wc
JOIN equipment_units AS eu ON eu.equipment_id = wc.equipment_id;

-- Parts catalog with supplier/manufacturer; cost and price columns deliberately excluded.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW vw_rag_parts_suppliers AS
SELECT
    pi.part_id,
    pi.part_name,
    pi.category,
    pi.manufacturer,
    pi.supplier,
    pi.stock_quantity,
    pi.reorder_level,
    CASE WHEN pi.stock_quantity < pi.reorder_level THEN 'Below reorder level' ELSE 'OK' END AS stock_status
FROM parts_inventory AS pi;

-- Evidence for the demo
SELECT table_name AS rag_view, view_definition IS NOT NULL AS defined
FROM information_schema.views
WHERE table_schema = 'gigavolt_silver' AND table_name LIKE 'vw\_rag\_%';

SELECT 'vw_rag_equipment' AS view_name, COUNT(*) AS row_count FROM vw_rag_equipment
UNION ALL SELECT 'vw_rag_service_history', COUNT(*) FROM vw_rag_service_history
UNION ALL SELECT 'vw_rag_warranty_claims', COUNT(*) FROM vw_rag_warranty_claims
UNION ALL SELECT 'vw_rag_parts_suppliers', COUNT(*) FROM vw_rag_parts_suppliers;
