-- Run on node 3. Execute once as warm-up and three times for measured results.
USE gigavolt_analytics;

SELECT
  (SELECT COUNT(*) FROM service_analytics_innodb) AS innodb_rows,
  (SELECT COUNT(*) FROM service_analytics_columnstore) AS columnstore_rows,
  (SELECT SUM(labor_cost) FROM service_analytics_innodb) AS innodb_total_labor,
  (SELECT SUM(labor_cost) FROM service_analytics_columnstore) AS columnstore_total_labor;

SET @started_at = NOW(6);
SELECT service_type,
       YEAR(dispatch_date) AS service_year,
       MONTH(dispatch_date) AS service_month,
       COUNT(*) AS dispatch_count,
       ROUND(SUM(hours_spent), 2) AS total_hours,
       ROUND(SUM(labor_cost), 2) AS total_labor_cost,
       ROUND(AVG(labor_cost), 2) AS avg_labor_cost
FROM service_analytics_innodb
GROUP BY service_type, YEAR(dispatch_date), MONTH(dispatch_date)
ORDER BY total_labor_cost DESC;
SELECT 'InnoDB' AS engine,
       ROUND(TIMESTAMPDIFF(MICROSECOND, @started_at, NOW(6)) / 1000000, 6) AS elapsed_seconds;

SET @started_at = NOW(6);
SELECT service_type,
       YEAR(dispatch_date) AS service_year,
       MONTH(dispatch_date) AS service_month,
       COUNT(*) AS dispatch_count,
       ROUND(SUM(hours_spent), 2) AS total_hours,
       ROUND(SUM(labor_cost), 2) AS total_labor_cost,
       ROUND(AVG(labor_cost), 2) AS avg_labor_cost
FROM service_analytics_columnstore
GROUP BY service_type, YEAR(dispatch_date), MONTH(dispatch_date)
ORDER BY total_labor_cost DESC;
SELECT 'ColumnStore' AS engine,
       ROUND(TIMESTAMPDIFF(MICROSECOND, @started_at, NOW(6)) / 1000000, 6) AS elapsed_seconds;
