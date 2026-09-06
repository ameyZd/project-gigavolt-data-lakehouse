DROP DATABASE IF EXISTS gigavolt_bronze;

CREATE DATABASE IF NOT EXISTS gigavolt_bronze
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE gigavolt_bronze;

-- Raw text only staging tables
DROP TABLE IF EXISTS bronze_customers;
CREATE TABLE bronze_customers (
    customer_id TEXT,
    company_name TEXT,
    contact_name TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    country TEXT,
    created_date TEXT,
    status TEXT
);

DROP TABLE IF EXISTS bronze_equipment;
CREATE TABLE bronze_equipment (
    equipment_id TEXT,
    customer_id TEXT,
    model TEXT,
    serial_number TEXT,
    install_date TEXT,
    capacity_kw TEXT,
    voltage TEXT,
    warranty_expiration TEXT,
    status TEXT,
    location_notes TEXT
);

DROP TABLE IF EXISTS bronze_dispatches;
CREATE TABLE bronze_dispatches (
    dispatch_id TEXT,
    equipment_id TEXT,
    customer_id TEXT,
    dispatch_date TEXT,
    technician TEXT,
    service_type TEXT,
    hours_spent TEXT,
    labor_cost TEXT,
    resolution_summary TEXT,
    status TEXT
);

DROP TABLE IF EXISTS bronze_parts;
CREATE TABLE bronze_parts (
    part_id TEXT,
    part_name TEXT,
    category TEXT,
    manufacturer TEXT,
    supplier TEXT,
    unit_cost TEXT,
    unit_price TEXT,
    stock_quantity TEXT,
    reorder_level TEXT
);

DROP TABLE IF EXISTS bronze_warranty_claims;
CREATE TABLE bronze_warranty_claims (
    claim_id TEXT,
    equipment_id TEXT,
    dispatch_id TEXT,
    part_id TEXT,
    claim_date TEXT,
    claim_amount TEXT,
    approved_amount TEXT,
    status TEXT,
    denial_reason TEXT,
    claim_notes TEXT
);

SET GLOBAL local_infile = 1;

LOAD DATA LOCAL INFILE '/opt/data/gigavolt/raw/customers.csv'
INTO TABLE bronze_customers
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

LOAD DATA LOCAL INFILE '/opt/data/gigavolt/raw/equipment.csv'
INTO TABLE bronze_equipment
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

LOAD DATA LOCAL INFILE '/opt/data/gigavolt/raw/dispatches.csv'
INTO TABLE bronze_dispatches
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

LOAD DATA LOCAL INFILE '/opt/data/gigavolt/raw/parts.csv'
INTO TABLE bronze_parts
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

LOAD DATA LOCAL INFILE '/opt/data/gigavolt/raw/warranty_claims.csv'
INTO TABLE bronze_warranty_claims
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

SELECT 'bronze_customers' AS table_name, COUNT(*) AS row_count FROM bronze_customers
UNION ALL
SELECT 'bronze_equipment', COUNT(*) FROM bronze_equipment
UNION ALL
SELECT 'bronze_dispatches', COUNT(*) FROM bronze_dispatches
UNION ALL
SELECT 'bronze_parts', COUNT(*) FROM bronze_parts
UNION ALL
SELECT 'bronze_warranty_claims', COUNT(*) FROM bronze_warranty_claims;