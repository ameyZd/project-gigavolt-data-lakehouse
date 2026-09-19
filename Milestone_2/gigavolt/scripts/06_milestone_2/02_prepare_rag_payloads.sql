-- Create provenance-preserving RAG payloads and the procedure that prepares them.

USE gigavolt_silver;

CREATE TABLE IF NOT EXISTS rag_service_payloads (
    payload_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    dispatch_id VARCHAR(20) NOT NULL,
    equipment_id VARCHAR(20) NOT NULL,
    source_table VARCHAR(64) NOT NULL DEFAULT 'service_logs',
    source_record_id VARCHAR(20) NOT NULL,
    payload_text LONGTEXT NOT NULL,
    prepared_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (payload_id),
    UNIQUE KEY uq_rag_payload_dispatch (dispatch_id),
    KEY idx_rag_payload_equipment (equipment_id),
    CONSTRAINT fk_rag_payload_dispatch
        FOREIGN KEY (dispatch_id) REFERENCES service_logs(dispatch_id),
    CONSTRAINT fk_rag_payload_equipment
        FOREIGN KEY (equipment_id) REFERENCES equipment_units(equipment_id)
) ENGINE=InnoDB;

DROP PROCEDURE IF EXISTS sp_Prepare_RAG_Payloads;
DELIMITER $$

CREATE PROCEDURE sp_Prepare_RAG_Payloads()
BEGIN
    -- If anything fails, preserve the last complete payload set.
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    -- The unique dispatch key makes this an idempotent upsert: rerunning the
    -- procedure refreshes payloads instead of creating duplicate embeddings.
    INSERT INTO rag_service_payloads (
        dispatch_id,
        equipment_id,
        source_table,
        source_record_id,
        payload_text,
        prepared_at
    )
    SELECT
        sl.dispatch_id,
        sl.equipment_id,
        'service_logs',
        sl.dispatch_id,
        CONCAT_WS('\n',
            'SOURCE: MariaDB.gigavolt_silver.service_logs',
            CONCAT('SOURCE_RECORD_ID: ', sl.dispatch_id),
            CONCAT('EQUIPMENT_ID: ', sl.equipment_id),
            CONCAT('EQUIPMENT_MODEL: ', COALESCE(eu.model, 'unknown')),
            CONCAT('CUSTOMER_ID: ', eu.customer_id),
            CONCAT('SERVICE_CONTEXT: ', sl.rag_service_context),
            CONCAT('CLEANED_RESOLUTION: ', COALESCE(NULLIF(TRIM(REGEXP_REPLACE(sl.resolution_summary, '[[:space:]]+', ' ')), ''), 'No resolution recorded'))
        ),
        CURRENT_TIMESTAMP
    FROM service_logs AS sl
    INNER JOIN equipment_units AS eu
        ON eu.equipment_id = sl.equipment_id
    ON DUPLICATE KEY UPDATE
        equipment_id = VALUES(equipment_id),
        source_table = VALUES(source_table),
        source_record_id = VALUES(source_record_id),
        payload_text = VALUES(payload_text),
        prepared_at = VALUES(prepared_at);

    COMMIT;

    -- Return compact evidence for the live demo; Step 5 displays sample text.
    SELECT COUNT(*) AS prepared_payload_count,
           MAX(prepared_at) AS last_prepared_at
    FROM rag_service_payloads;
END$$
DELIMITER ;

-- Payload build.
CALL sp_Prepare_RAG_Payloads();
  
-- Evidence
SELECT payload_id, dispatch_id, equipment_id, payload_text, prepared_at
FROM rag_service_payloads
LIMIT 5;
