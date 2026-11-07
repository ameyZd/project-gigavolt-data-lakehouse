-- Milestone 5: prepare the Milestone 1 Silver ERP schema for RAG retrieval.
-- Run as: sudo mariadb < sql/rag_setup.sql

USE gigavolt_silver;

-- Indexes for the metadata filters the hybrid retriever uses.
-- (FK columns such as service_logs.equipment_id are already indexed by InnoDB.)
CREATE INDEX IF NOT EXISTS idx_equipment_model      ON equipment_units (model);
CREATE INDEX IF NOT EXISTS idx_equipment_serial     ON equipment_units (serial_number);
CREATE INDEX IF NOT EXISTS idx_equipment_warranty   ON equipment_units (warranty_expiration);
CREATE INDEX IF NOT EXISTS idx_service_type_date    ON service_logs (service_type, dispatch_date);
CREATE INDEX IF NOT EXISTS idx_claims_status_date   ON warranty_claims (status, claim_date);
CREATE INDEX IF NOT EXISTS idx_parts_name           ON parts_inventory (part_name);

-- Least privilege: the RAG pipeline can only read the Silver layer.
CREATE USER IF NOT EXISTS 'rag_reader'@'localhost' IDENTIFIED BY 'gigavolt_rag_readonly';
GRANT SELECT ON gigavolt_silver.* TO 'rag_reader'@'localhost';
FLUSH PRIVILEGES;

SELECT 'equipment_units' AS table_name, COUNT(*) AS row_count FROM equipment_units
UNION ALL SELECT 'service_logs', COUNT(*) FROM service_logs
UNION ALL SELECT 'warranty_claims', COUNT(*) FROM warranty_claims
UNION ALL SELECT 'parts_inventory', COUNT(*) FROM parts_inventory;
