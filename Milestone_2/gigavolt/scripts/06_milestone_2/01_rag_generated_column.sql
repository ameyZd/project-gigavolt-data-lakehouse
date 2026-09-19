-- Create a dynamic, readable service-log context for future RAG payloads.
-- Run once while connected to MariaDB as an account that can ALTER tables.

USE gigavolt_silver;

ALTER TABLE service_logs
    ADD COLUMN rag_service_context TEXT
    GENERATED ALWAYS AS (
        CONCAT_WS(' | ',
            CONCAT('Dispatch ID: ', dispatch_id),
            CONCAT('Equipment ID: ', equipment_id),
            CONCAT('Service Date: ', COALESCE(CAST(dispatch_date AS CHAR), 'unknown')),
            CONCAT('Technician: ', COALESCE(technician, 'unknown')),
            CONCAT('Service Type: ', COALESCE(service_type, 'unknown')),
            CONCAT('Resolution: ', COALESCE(NULLIF(TRIM(resolution_summary), ''), 'No resolution recorded')),
            CONCAT('Status: ', COALESCE(status, 'unknown'))
        )
    ) VIRTUAL;


-- Proof that the column is virtual
DESCRIBE service_logs;
 
-- Proof that MariaDB builds readable RAG context from each service-log row.
SELECT dispatch_id, rag_service_context
FROM service_logs
ORDER BY dispatch_date DESC, dispatch_id
LIMIT 5;

