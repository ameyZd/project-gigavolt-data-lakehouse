USE gigavolt_silver;

-- Loading parent tables.
LOAD DATA LOCAL INFILE '/opt/data/gigavolt/clean/customers_clean.csv'
INTO TABLE customers
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(@customer_id, @company_name, @contact_name, @phone, @email, @address,
 @city, @state, @zip, @country, @created_date, @status)
SET
  customer_id = NULLIF(@customer_id, ''),
  company_name = NULLIF(@company_name, ''),
  contact_name = NULLIF(@contact_name, ''),
  phone = NULLIF(@phone, ''),
  email = NULLIF(@email, ''),
  address = NULLIF(@address, ''),
  city = NULLIF(@city, ''),
  state = NULLIF(@state, ''),
  zip = NULLIF(@zip, ''),
  country = NULLIF(@country, ''),
  created_date = NULLIF(@created_date, ''),
  status = NULLIF(@status, '');


LOAD DATA LOCAL INFILE '/opt/data/gigavolt/clean/equipment_clean.csv'
INTO TABLE equipment_units
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(@equipment_id, @customer_id, @model, @serial_number, @install_date,
 @capacity_kw, @voltage, @warranty_expiration, @status, @location_notes)
SET
  equipment_id = NULLIF(@equipment_id, ''),
  customer_id = NULLIF(@customer_id, ''),
  model = NULLIF(@model, ''),
  serial_number = NULLIF(@serial_number, ''),
  install_date = NULLIF(@install_date, ''),
  capacity_kw = NULLIF(@capacity_kw, ''),
  voltage = NULLIF(@voltage, ''),
  warranty_expiration = NULLIF(@warranty_expiration, ''),
  status = NULLIF(@status, ''),
  location_notes = NULLIF(@location_notes, '');


LOAD DATA LOCAL INFILE '/opt/data/gigavolt/clean/parts_clean.csv'
INTO TABLE parts_inventory
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(@part_id, @part_name, @category, @manufacturer, @supplier, @unit_cost,
 @unit_price, @stock_quantity, @reorder_level)
SET
  part_id = NULLIF(@part_id, ''),
  part_name = NULLIF(@part_name, ''),
  category = NULLIF(@category, ''),
  manufacturer = NULLIF(@manufacturer, ''),
  supplier = NULLIF(@supplier, ''),
  unit_cost = NULLIF(@unit_cost, ''),
  unit_price = NULLIF(@unit_price, ''),
  stock_quantity = NULLIF(@stock_quantity, ''),
  reorder_level = NULLIF(@reorder_level, '');

-- customer_id exists in the clean dispatch file but is omitted here:
-- it is functionally determined through equipment_units.customer_id.
LOAD DATA LOCAL INFILE '/opt/data/gigavolt/clean/dispatches_clean.csv'
INTO TABLE service_logs
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(@dispatch_id, @equipment_id, @unused_customer_id, @dispatch_date,
 @technician, @service_type, @hours_spent, @labor_cost,
 @resolution_summary, @status)
SET
  dispatch_id = NULLIF(@dispatch_id, ''),
  equipment_id = NULLIF(@equipment_id, ''),
  dispatch_date = NULLIF(@dispatch_date, ''),
  technician = NULLIF(@technician, ''),
  service_type = NULLIF(@service_type, ''),
  hours_spent = NULLIF(@hours_spent, ''),
  labor_cost = NULLIF(@labor_cost, ''),
  resolution_summary = NULLIF(@resolution_summary, ''),
  status = NULLIF(@status, '');


LOAD DATA LOCAL INFILE '/opt/data/gigavolt/clean/warranty_claims_clean.csv'
INTO TABLE warranty_claims
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(@claim_id, @equipment_id, @dispatch_id, @part_id, @claim_date,
 @claim_amount, @approved_amount, @status, @denial_reason, @claim_notes)
SET
  claim_id = NULLIF(@claim_id, ''),
  equipment_id = NULLIF(@equipment_id, ''),
  dispatch_id = NULLIF(@dispatch_id, ''),
  part_id = NULLIF(@part_id, ''),
  claim_date = NULLIF(@claim_date, ''),
  claim_amount = NULLIF(@claim_amount, ''),
  approved_amount = NULLIF(@approved_amount, ''),
  status = NULLIF(@status, ''),
  denial_reason = NULLIF(@denial_reason, ''),
  claim_notes = NULLIF(@claim_notes, '');


SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM customers
UNION ALL
SELECT 'equipment_units', COUNT(*) FROM equipment_units
UNION ALL
SELECT 'parts_inventory', COUNT(*) FROM parts_inventory
UNION ALL
SELECT 'service_logs', COUNT(*) FROM service_logs
UNION ALL
SELECT 'warranty_claims', COUNT(*) FROM warranty_claims;
