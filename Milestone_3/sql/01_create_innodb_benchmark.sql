-- Run once on node 1 after all Galera nodes report Primary/Synced.
-- Creates a physical materialized-view-style snapshot from existing service logs
-- and a matching InnoDB benchmark table. Galera replicates these InnoDB changes.

CREATE DATABASE IF NOT EXISTS gigavolt_analytics;
USE gigavolt_analytics;

DROP TABLE IF EXISTS service_analytics_mv;
CREATE TABLE service_analytics_mv (
    benchmark_row_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_dispatch_id VARCHAR(20) NOT NULL,
    equipment_id VARCHAR(20) NOT NULL,
    dispatch_date DATE,
    service_type VARCHAR(100),
    status VARCHAR(30),
    hours_spent DECIMAL(10,2),
    labor_cost DECIMAL(12,2),
    PRIMARY KEY (benchmark_row_id)
) ENGINE=InnoDB;

-- The existing 4,864 service logs are duplicated 100 times, yielding 486,400
-- reproducible benchmark rows without adding a new external data source.
INSERT INTO service_analytics_mv
    (source_dispatch_id, equipment_id, dispatch_date, service_type,
     status, hours_spent, labor_cost)
WITH RECURSIVE multiplier (n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM multiplier WHERE n < 100
)
SELECT dispatch_id, equipment_id, dispatch_date, service_type,
       status, hours_spent, labor_cost
FROM gigavolt_silver.service_logs
CROSS JOIN multiplier;

DROP TABLE IF EXISTS service_analytics_innodb;
CREATE TABLE service_analytics_innodb (
    benchmark_row_id BIGINT UNSIGNED NOT NULL,
    source_dispatch_id VARCHAR(20) NOT NULL,
    equipment_id VARCHAR(20) NOT NULL,
    dispatch_date DATE,
    service_type VARCHAR(100),
    status VARCHAR(30),
    hours_spent DECIMAL(10,2),
    labor_cost DECIMAL(12,2),
    PRIMARY KEY (benchmark_row_id),
    KEY idx_service_type (service_type),
    KEY idx_dispatch_date (dispatch_date)
) ENGINE=InnoDB;

INSERT INTO service_analytics_innodb
SELECT * FROM service_analytics_mv;

SELECT COUNT(*) AS benchmark_rows,
       SUM(labor_cost) AS total_labor_cost
FROM service_analytics_innodb;
