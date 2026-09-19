-- Append-only audit history for service-log inserts and updates.

USE gigavolt_silver;

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    database_user VARCHAR(288) NOT NULL,
    table_name VARCHAR(64) NOT NULL,
    record_id VARCHAR(100) NOT NULL,
    operation_type ENUM('INSERT', 'UPDATE') NOT NULL,
    old_values JSON NULL,
    new_values JSON NULL,
    change_description VARCHAR(500) NOT NULL,
    PRIMARY KEY (audit_id),
    KEY idx_audit_record_lookup (table_name, record_id, event_timestamp),
    KEY idx_audit_event_time (event_timestamp)
) ENGINE=InnoDB;

-- Drop only these named triggers so this script is safe to re-run.
DROP TRIGGER IF EXISTS trg_service_logs_after_insert_audit;
DROP TRIGGER IF EXISTS trg_service_logs_before_update_audit;
DROP TRIGGER IF EXISTS trg_audit_logs_before_update_immutable;
DROP TRIGGER IF EXISTS trg_audit_logs_before_delete_immutable;

DELIMITER $$

-- AFTER INSERT: the new service record has been accepted by the database.
CREATE TRIGGER trg_service_logs_after_insert_audit
AFTER INSERT ON service_logs
FOR EACH ROW
BEGIN
    INSERT INTO audit_logs (
        database_user, table_name, record_id, operation_type,
        old_values, new_values, change_description
    ) VALUES (
        CURRENT_USER(), 'service_logs', NEW.dispatch_id, 'INSERT', NULL,
        JSON_OBJECT(
            'dispatch_id', NEW.dispatch_id,
            'equipment_id', NEW.equipment_id,
            'dispatch_date', NEW.dispatch_date,
            'technician', NEW.technician,
            'service_type', NEW.service_type,
            'hours_spent', NEW.hours_spent,
            'labor_cost', NEW.labor_cost,
            'resolution_summary', NEW.resolution_summary,
            'status', NEW.status
        ),
        'New service log created'
    );
END$$

-- BEFORE UPDATE: record the old values before they are changed, along with the
-- requested replacement values from NEW.
CREATE TRIGGER trg_service_logs_before_update_audit
BEFORE UPDATE ON service_logs
FOR EACH ROW
BEGIN
    INSERT INTO audit_logs (
        database_user, table_name, record_id, operation_type,
        old_values, new_values, change_description
    ) VALUES (
        CURRENT_USER(), 'service_logs', OLD.dispatch_id, 'UPDATE',
        JSON_OBJECT(
            'dispatch_id', OLD.dispatch_id,
            'equipment_id', OLD.equipment_id,
            'dispatch_date', OLD.dispatch_date,
            'technician', OLD.technician,
            'service_type', OLD.service_type,
            'hours_spent', OLD.hours_spent,
            'labor_cost', OLD.labor_cost,
            'resolution_summary', OLD.resolution_summary,
            'status', OLD.status
        ),
        JSON_OBJECT(
            'dispatch_id', NEW.dispatch_id,
            'equipment_id', NEW.equipment_id,
            'dispatch_date', NEW.dispatch_date,
            'technician', NEW.technician,
            'service_type', NEW.service_type,
            'hours_spent', NEW.hours_spent,
            'labor_cost', NEW.labor_cost,
            'resolution_summary', NEW.resolution_summary,
            'status', NEW.status
        ),
        'Service log changed; pre-change and post-change values captured'
    );
END$$

-- Make audit history append-only. A non-DBA cannot alter or delete past rows.
CREATE TRIGGER trg_audit_logs_before_update_immutable
BEFORE UPDATE ON audit_logs
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'audit_logs is append-only; updates are prohibited';
END$$

CREATE TRIGGER trg_audit_logs_before_delete_immutable
BEFORE DELETE ON audit_logs
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'audit_logs is append-only; deletes are prohibited';
END$$

DELIMITER ;

-- Proof that all four triggers were created.
SHOW TRIGGERS FROM gigavolt_silver\G;

