USE gigavolt_bronze;

SHOW TABLES;

SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM bronze_customers
UNION ALL
SELECT 'equipment', COUNT(*) FROM bronze_equipment
UNION ALL
SELECT 'dispatches', COUNT(*) FROM bronze_dispatches
UNION ALL
SELECT 'parts', COUNT(*) FROM bronze_parts
UNION ALL
SELECT 'warranty_claims', COUNT(*) FROM bronze_warranty_claims;

SELECT * FROM bronze_customers LIMIT 5;
SELECT * FROM bronze_equipment LIMIT 5;
SELECT * FROM bronze_dispatches LIMIT 5;
SELECT * FROM bronze_parts LIMIT 5;
SELECT * FROM bronze_warranty_claims LIMIT 5;

