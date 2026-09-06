USE gigavolt_bronze;

-- 5 records from every raw table
SELECT * FROM bronze_customers LIMIT 5;
SELECT * FROM bronze_equipment LIMIT 5;
SELECT * FROM bronze_dispatches LIMIT 5;
SELECT * FROM bronze_parts LIMIT 5;
SELECT * FROM bronze_warranty_claims LIMIT 5;

-- Date-format variations
SELECT created_date, COUNT(*) AS occurrences
FROM bronze_customers
GROUP BY created_date
ORDER BY occurrences DESC
LIMIT 15;

SELECT install_date, COUNT(*) AS occurrences
FROM bronze_equipment
GROUP BY install_date
ORDER BY occurrences DESC
LIMIT 15;

-- Duplicate business IDs
SELECT customer_id, COUNT(*) AS copies
FROM bronze_customers
GROUP BY customer_id
HAVING COUNT(*) > 1
LIMIT 10;

SELECT equipment_id, COUNT(*) AS copies
FROM bronze_equipment
GROUP BY equipment_id
HAVING COUNT(*) > 1
LIMIT 10;

-- Extra whitespace and HTML in text fields
SELECT COUNT(*) AS dispatch_rows_with_extra_whitespace
FROM bronze_dispatches
WHERE technician <> TRIM(technician)
   OR resolution_summary <> TRIM(resolution_summary);

SELECT COUNT(*) AS dispatch_rows_with_html
FROM bronze_dispatches
WHERE resolution_summary REGEXP '<[^>]*>';
SQL

