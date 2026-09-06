USE gigavolt_silver;

-- 1. Row-count reconciliation
SELECT 'customers' AS table_name,
       (SELECT COUNT(*) FROM gigavolt_bronze.bronze_customers) AS bronze_rows,
       (SELECT COUNT(*) FROM customers) AS silver_rows,
       25 AS quarantined_rows
UNION ALL
SELECT 'equipment_units',
       (SELECT COUNT(*) FROM gigavolt_bronze.bronze_equipment),
       (SELECT COUNT(*) FROM equipment_units),
       75
UNION ALL
SELECT 'service_logs',
       (SELECT COUNT(*) FROM gigavolt_bronze.bronze_dispatches),
       (SELECT COUNT(*) FROM service_logs),
       136
UNION ALL
SELECT 'parts_inventory',
       (SELECT COUNT(*) FROM gigavolt_bronze.bronze_parts),
       (SELECT COUNT(*) FROM parts_inventory),
       0
UNION ALL
SELECT 'warranty_claims',
       (SELECT COUNT(*) FROM gigavolt_bronze.bronze_warranty_claims),
       (SELECT COUNT(*) FROM warranty_claims),
       77;

-- 2. Required foreign-key integrity checks: every result must be zero.
SELECT 'equipment_units -> customers' AS relationship_name,
       COUNT(*) AS orphan_rows
FROM equipment_units e
LEFT JOIN customers c ON c.customer_id = e.customer_id
WHERE c.customer_id IS NULL

UNION ALL

SELECT 'service_logs -> equipment_units',
       COUNT(*)
FROM service_logs s
LEFT JOIN equipment_units e ON e.equipment_id = s.equipment_id
WHERE e.equipment_id IS NULL

UNION ALL

SELECT 'warranty_claims -> equipment_units',
       COUNT(*)
FROM warranty_claims w
LEFT JOIN equipment_units e ON e.equipment_id = w.equipment_id
WHERE e.equipment_id IS NULL

UNION ALL

SELECT 'warranty_claims -> service_logs (optional)',
       COUNT(*)
FROM warranty_claims w
LEFT JOIN service_logs s ON s.dispatch_id = w.dispatch_id
WHERE w.dispatch_id IS NOT NULL
  AND s.dispatch_id IS NULL

UNION ALL

SELECT 'warranty_claims -> parts_inventory (optional)',
       COUNT(*)
FROM warranty_claims w
LEFT JOIN parts_inventory p ON p.part_id = w.part_id
WHERE w.part_id IS NOT NULL
  AND p.part_id IS NULL;

-- 3. Final cleanup checks: each result should be zero.
SELECT 'HTML remaining in customer company names' AS check_name,
       COUNT(*) AS problem_rows
FROM customers
WHERE company_name REGEXP '<[^>]*>'

UNION ALL

SELECT 'HTML remaining in service summaries',
       COUNT(*)
FROM service_logs
WHERE resolution_summary REGEXP '<[^>]*>'

UNION ALL

SELECT 'invalid customer created dates',
       COUNT(*)
FROM customers
WHERE created_date IS NULL;

