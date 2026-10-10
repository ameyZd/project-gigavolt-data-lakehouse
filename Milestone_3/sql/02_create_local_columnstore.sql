-- Run on node 3 only after 01_create_innodb_benchmark.sql is replicated.
-- This table is deliberately local-only: ColumnStore is not tested as a Galera
-- replicated, transaction-safe workload for this milestone.

SET SESSION wsrep_on = OFF;
USE gigavolt_analytics;

DROP TABLE IF EXISTS service_analytics_columnstore;
CREATE TABLE service_analytics_columnstore (
    benchmark_row_id BIGINT UNSIGNED NOT NULL,
    source_dispatch_id VARCHAR(20) NOT NULL,
    equipment_id VARCHAR(20) NOT NULL,
    dispatch_date DATE,
    service_type VARCHAR(100),
    status VARCHAR(30),
    hours_spent DECIMAL(10,2),
    labor_cost DECIMAL(12,2)
) ENGINE=ColumnStore;

INSERT INTO service_analytics_columnstore
SELECT * FROM service_analytics_mv;

SET SESSION wsrep_on = ON;

SELECT COUNT(*) AS benchmark_rows,
       SUM(labor_cost) AS total_labor_cost
FROM service_analytics_columnstore;
