-- Give support/RAG readers operational context without customer details.

USE gigavolt_silver;

-- This view intentionally does NOT expose customer details or any
-- warranty/financial columns. It also omits customer_id to minimize identity
-- exposure in the reader-facing dataset.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW vw_service_rag_safe AS
SELECT
    sl.dispatch_id,
    sl.equipment_id,
    eu.model AS equipment_model,
    eu.serial_number,
    sl.dispatch_date,
    sl.technician,
    sl.service_type,
    sl.resolution_summary,
    sl.status,
    sl.rag_service_context
FROM service_logs AS sl
INNER JOIN equipment_units AS eu
    ON eu.equipment_id = sl.equipment_id;

-- This role has no direct SELECT grant on customers, equipment_units,
-- service_logs, warranty_claims, audit_logs, or rag_service_payloads.
-- It can query only the deliberately limited view above.
CREATE ROLE IF NOT EXISTS 'gigavolt_support_reader';
GRANT SELECT ON gigavolt_silver.vw_service_rag_safe
    TO 'gigavolt_support_reader';



-- Evidence for the demo: these are the only columns exposed to this role.
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'gigavolt_silver'
  AND table_name = 'vw_service_rag_safe'
ORDER BY ordinal_position\G;
 
SHOW GRANTS FOR 'gigavolt_support_reader';
 
SELECT *
FROM vw_service_rag_safe
LIMIT 5\G;
