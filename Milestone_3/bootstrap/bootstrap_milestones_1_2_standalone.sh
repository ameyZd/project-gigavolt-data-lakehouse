#!/usr/bin/env bash
# Project Gigavolt | Self-contained Milestone 1 + 2 rebuild for node 1.
# Run only after node 1 has been bootstrapped as a Galera Primary component.
# This intentionally drops and recreates gigavolt_bronze and gigavolt_silver.
set -Eeuo pipefail

[[ ${1:-} == --reset ]] || {
  echo "Usage: sudo bash $0 --reset" >&2
  echo 'This drops and rebuilds gigavolt_bronze and gigavolt_silver.' >&2
  exit 64
}
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }

DATA_ROOT=/opt/data/gigavolt
RAW_DIR="$DATA_ROOT/raw"
CLEAN_DIR="$DATA_ROOT/clean"
export DEBIAN_FRONTEND=noninteractive

command -v mariadb >/dev/null || { echo 'MariaDB must be installed and running first.' >&2; exit 1; }
mariadb --protocol=socket -NBe "SELECT 1" >/dev/null || {
  echo 'MariaDB is not accepting local connections. Bootstrap node 1 first.' >&2; exit 1;
}

apt-get update -y
apt-get install -y python3 curl unzip
install -d -m 0755 "$RAW_DIR" "$CLEAN_DIR/rejected"
curl --fail --location --retry 3 'https://622.gomillion.org/data/fall2026.zip' -o "$RAW_DIR/fall2026.zip"
unzip -o "$RAW_DIR/fall2026.zip" -d "$RAW_DIR"
for file in customers.csv equipment.csv dispatches.csv parts.csv warranty_claims.csv; do
  [[ -f "$RAW_DIR/$file" ]] || { echo "Missing $file after download." >&2; exit 1; }
done

# Bronze: keep raw source values before cleansing.
mariadb --protocol=socket --local-infile=1 <<SQL
SET GLOBAL local_infile=1;
DROP DATABASE IF EXISTS gigavolt_bronze;
CREATE DATABASE gigavolt_bronze CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE gigavolt_bronze;
CREATE TABLE bronze_customers(customer_id TEXT,company_name TEXT,contact_name TEXT,phone TEXT,email TEXT,address TEXT,city TEXT,state TEXT,zip TEXT,country TEXT,created_date TEXT,status TEXT);
CREATE TABLE bronze_equipment(equipment_id TEXT,customer_id TEXT,model TEXT,serial_number TEXT,install_date TEXT,capacity_kw TEXT,voltage TEXT,warranty_expiration TEXT,status TEXT,location_notes TEXT);
CREATE TABLE bronze_dispatches(dispatch_id TEXT,equipment_id TEXT,customer_id TEXT,dispatch_date TEXT,technician TEXT,service_type TEXT,hours_spent TEXT,labor_cost TEXT,resolution_summary TEXT,status TEXT);
CREATE TABLE bronze_parts(part_id TEXT,part_name TEXT,category TEXT,manufacturer TEXT,supplier TEXT,unit_cost TEXT,unit_price TEXT,stock_quantity TEXT,reorder_level TEXT);
CREATE TABLE bronze_warranty_claims(claim_id TEXT,equipment_id TEXT,dispatch_id TEXT,part_id TEXT,claim_date TEXT,claim_amount TEXT,approved_amount TEXT,status TEXT,denial_reason TEXT,claim_notes TEXT);
LOAD DATA LOCAL INFILE '$RAW_DIR/customers.csv' INTO TABLE bronze_customers FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES;
LOAD DATA LOCAL INFILE '$RAW_DIR/equipment.csv' INTO TABLE bronze_equipment FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES;
LOAD DATA LOCAL INFILE '$RAW_DIR/dispatches.csv' INTO TABLE bronze_dispatches FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES;
LOAD DATA LOCAL INFILE '$RAW_DIR/parts.csv' INTO TABLE bronze_parts FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES;
LOAD DATA LOCAL INFILE '$RAW_DIR/warranty_claims.csv' INTO TABLE bronze_warranty_claims FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES;
SQL

# Clean values, normalize formats, remove duplicate primary keys, and reject orphan rows.
RAW_DIR="$RAW_DIR" CLEAN_DIR="$CLEAN_DIR" python3 - <<'PY'
import csv, html, os, re, unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

raw, clean = Path(os.environ['RAW_DIR']), Path(os.environ['CLEAN_DIR'])
reject = clean / 'rejected'; reject.mkdir(exist_ok=True)
cfg = {
 'customers': ('customers.csv','customer_id',['created_date'],[]),
 'equipment': ('equipment.csv','equipment_id',['install_date','warranty_expiration'],['capacity_kw']),
 'dispatches': ('dispatches.csv','dispatch_id',['dispatch_date'],['hours_spent','labor_cost']),
 'parts': ('parts.csv','part_id',[],['unit_cost','unit_price','stock_quantity','reorder_level']),
 'warranty_claims': ('warranty_claims.csv','claim_id',['claim_date'],['claim_amount','approved_amount']),
}
html_re, space = re.compile(r'<[^>]*>'), re.compile(r'\s+')
nulls = {'','NULL','N/A','NA'}
def text(v):
    v = space.sub(' ', html_re.sub('', unicodedata.normalize('NFKC', html.unescape('' if v is None else str(v))))).strip()
    return '' if v.upper() in nulls else v
def date(v):
    v=text(v)
    if not v: return ''
    for fmt in ('%Y-%m-%dT%H:%M:%SZ','%Y-%m-%d','%Y/%m/%d','%m/%d/%Y','%m-%d-%Y'):
        try: return datetime.strptime(v,fmt).strftime('%Y-%m-%d')
        except ValueError: pass
    raise ValueError('invalid date')
def number(v):
    v=text(v).replace('$','').replace(',','')
    return '' if not v else format(Decimal(v).normalize(),'f')
def write(path, rows, fields):
    with path.open('w',encoding='utf-8',newline='') as fh:
        w=csv.DictWriter(fh,fieldnames=fields); w.writeheader()
        w.writerows({k:r.get(k,'') for k in fields} for r in rows)

data, rejected, totals = {}, defaultdict(list), {}
for name,(filename,pk,date_cols,num_cols) in cfg.items():
    with (raw/filename).open(encoding='utf-8-sig',errors='replace',newline='') as fh:
        rows=list(csv.DictReader(fh))
    totals[name]=len(rows); groups=defaultdict(list)
    for row_no,source in enumerate(rows,2):
        try:
            row={k:text(v) for k,v in source.items()}
            for col in date_cols: row[col]=date(row[col])
            for col in num_cols: row[col]=number(row[col])
            if row.get('status'): row['status']=row['status'].title()
            if row.get('state'): row['state']=row['state'].upper()
            if 'country' in row: row['country']={'USA':'US','U.S.':'US','UNITED STATES':'US'}.get(row['country'].upper(),row['country'].upper())
            row['_source_row']=row_no
            if not row.get(pk): raise ValueError('missing primary key')
            groups[row[pk]].append(row)
        except Exception as exc: rejected[name].append((dict(source,_source_row=row_no),str(exc)))
    data[name]=[]
    for key,dupes in groups.items():
        keep=max(dupes,key=lambda r:sum(bool(v) for k,v in r.items() if k!='_source_row'))
        data[name].append(keep)
        rejected[name] += [(r,f'duplicate {pk}: {key}') for r in dupes if r is not keep]
def keep(name, predicate, reason):
    good=[]
    for row in data[name]:
        if predicate(row): good.append(row)
        else: rejected[name].append((row,reason))
    data[name]=good
customers={r['customer_id'] for r in data['customers']}
keep('equipment',lambda r:r['customer_id'] in customers,'customer_id not found')
equipment={r['equipment_id'] for r in data['equipment']}
keep('dispatches',lambda r:r['equipment_id'] in equipment and r['customer_id'] in customers,'equipment_id or customer_id not found')
dispatches={r['dispatch_id'] for r in data['dispatches']}; parts={r['part_id'] for r in data['parts']}
keep('warranty_claims',lambda r:r['equipment_id'] in equipment and (not r['dispatch_id'] or r['dispatch_id'] in dispatches) and (not r['part_id'] or r['part_id'] in parts),'foreign key not found')
for name in cfg:
    fields=[k for k in data[name][0] if k!='_source_row']
    write(clean/f'{name}_clean.csv',data[name],fields)
    bad=[dict(r,source_row=r.get('_source_row',''),rejection_reason=reason) for r,reason in rejected[name]]
    write(reject/f'{name}_rejected.csv',bad,['source_row','rejection_reason']+fields)
(clean/'cleaning_summary.txt').write_text('\n'.join(f'{name}: input={totals[name]}, clean={len(data[name])}, rejected={len(rejected[name])}' for name in cfg)+'\n')
PY

# Silver: normalized InnoDB schema that Galera can replicate.
mariadb --protocol=socket --local-infile=1 <<SQL
DROP DATABASE IF EXISTS gigavolt_silver;
CREATE DATABASE gigavolt_silver CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE gigavolt_silver;
CREATE TABLE customers(customer_id VARCHAR(20) PRIMARY KEY,company_name VARCHAR(255) NOT NULL,contact_name VARCHAR(255) NOT NULL,phone VARCHAR(30),email VARCHAR(255),address VARCHAR(500),city VARCHAR(100),state CHAR(2),zip VARCHAR(20),country CHAR(2),created_date DATE,status VARCHAR(30)) ENGINE=InnoDB;
CREATE TABLE equipment_units(equipment_id VARCHAR(20) PRIMARY KEY,customer_id VARCHAR(20) NOT NULL,model VARCHAR(100),serial_number VARCHAR(100),install_date DATE,capacity_kw DECIMAL(12,2),voltage VARCHAR(30),warranty_expiration DATE,status VARCHAR(30),location_notes TEXT,FOREIGN KEY(customer_id) REFERENCES customers(customer_id)) ENGINE=InnoDB;
CREATE TABLE parts_inventory(part_id VARCHAR(20) PRIMARY KEY,part_name VARCHAR(255) NOT NULL,category VARCHAR(100),manufacturer VARCHAR(150),supplier VARCHAR(150),unit_cost DECIMAL(12,2),unit_price DECIMAL(12,2),stock_quantity INT,reorder_level INT) ENGINE=InnoDB;
CREATE TABLE service_logs(dispatch_id VARCHAR(20) PRIMARY KEY,equipment_id VARCHAR(20) NOT NULL,dispatch_date DATE,technician VARCHAR(255),service_type VARCHAR(100),hours_spent DECIMAL(10,2),labor_cost DECIMAL(12,2),resolution_summary TEXT,status VARCHAR(30),FOREIGN KEY(equipment_id) REFERENCES equipment_units(equipment_id)) ENGINE=InnoDB;
CREATE TABLE warranty_claims(claim_id VARCHAR(20) PRIMARY KEY,equipment_id VARCHAR(20) NOT NULL,dispatch_id VARCHAR(20),part_id VARCHAR(20),claim_date DATE,claim_amount DECIMAL(12,2),approved_amount DECIMAL(12,2),status VARCHAR(30),denial_reason TEXT,claim_notes TEXT,FOREIGN KEY(equipment_id) REFERENCES equipment_units(equipment_id),FOREIGN KEY(dispatch_id) REFERENCES service_logs(dispatch_id),FOREIGN KEY(part_id) REFERENCES parts_inventory(part_id)) ENGINE=InnoDB;
LOAD DATA LOCAL INFILE '$CLEAN_DIR/customers_clean.csv' INTO TABLE customers FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES;
LOAD DATA LOCAL INFILE '$CLEAN_DIR/equipment_clean.csv' INTO TABLE equipment_units FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES;
LOAD DATA LOCAL INFILE '$CLEAN_DIR/parts_clean.csv' INTO TABLE parts_inventory FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES;
LOAD DATA LOCAL INFILE '$CLEAN_DIR/dispatches_clean.csv' INTO TABLE service_logs FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES (@id,@equipment,@customer,@date,@technician,@type,@hours,@cost,@resolution,@status) SET dispatch_id=NULLIF(@id,''),equipment_id=NULLIF(@equipment,''),dispatch_date=NULLIF(@date,''),technician=NULLIF(@technician,''),service_type=NULLIF(@type,''),hours_spent=NULLIF(@hours,''),labor_cost=NULLIF(@cost,''),resolution_summary=NULLIF(@resolution,''),status=NULLIF(@status,'');
LOAD DATA LOCAL INFILE '$CLEAN_DIR/warranty_claims_clean.csv' INTO TABLE warranty_claims FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES;
SQL

# Milestone 2: generated RAG context, payloads, append-only audit, and safe view.
mariadb --protocol=socket <<'SQL'
USE gigavolt_silver;
ALTER TABLE service_logs ADD COLUMN rag_service_context TEXT GENERATED ALWAYS AS (CONCAT_WS(' | ',CONCAT('Dispatch ID: ',dispatch_id),CONCAT('Equipment ID: ',equipment_id),CONCAT('Service Date: ',COALESCE(CAST(dispatch_date AS CHAR),'unknown')),CONCAT('Technician: ',COALESCE(technician,'unknown')),CONCAT('Service Type: ',COALESCE(service_type,'unknown')),CONCAT('Resolution: ',COALESCE(NULLIF(TRIM(resolution_summary),''),'No resolution recorded')),CONCAT('Status: ',COALESCE(status,'unknown')))) VIRTUAL;
CREATE TABLE rag_service_payloads(payload_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,dispatch_id VARCHAR(20) NOT NULL,equipment_id VARCHAR(20) NOT NULL,source_table VARCHAR(64) NOT NULL DEFAULT 'service_logs',source_record_id VARCHAR(20) NOT NULL,payload_text LONGTEXT NOT NULL,prepared_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(payload_id),UNIQUE KEY uq_rag_payload_dispatch(dispatch_id),KEY idx_rag_payload_equipment(equipment_id),FOREIGN KEY(dispatch_id) REFERENCES service_logs(dispatch_id),FOREIGN KEY(equipment_id) REFERENCES equipment_units(equipment_id)) ENGINE=InnoDB;
CREATE TABLE audit_logs(audit_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,event_timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,database_user VARCHAR(288) NOT NULL,table_name VARCHAR(64) NOT NULL,record_id VARCHAR(100) NOT NULL,operation_type ENUM('INSERT','UPDATE') NOT NULL,old_values JSON NULL,new_values JSON NULL,change_description VARCHAR(500) NOT NULL,PRIMARY KEY(audit_id),KEY idx_audit_record_lookup(table_name,record_id,event_timestamp),KEY idx_audit_event_time(event_timestamp)) ENGINE=InnoDB;
DELIMITER $$
CREATE PROCEDURE sp_Prepare_RAG_Payloads()
BEGIN
  INSERT INTO rag_service_payloads(dispatch_id,equipment_id,source_table,source_record_id,payload_text,prepared_at)
  SELECT sl.dispatch_id,sl.equipment_id,'service_logs',sl.dispatch_id,
    CONCAT_WS('\n','SOURCE: MariaDB.gigavolt_silver.service_logs',CONCAT('SOURCE_RECORD_ID: ',sl.dispatch_id),CONCAT('EQUIPMENT_ID: ',sl.equipment_id),CONCAT('EQUIPMENT_MODEL: ',COALESCE(eu.model,'unknown')),CONCAT('CUSTOMER_ID: ',eu.customer_id),CONCAT('SERVICE_CONTEXT: ',sl.rag_service_context),CONCAT('CLEANED_RESOLUTION: ',COALESCE(NULLIF(TRIM(REGEXP_REPLACE(sl.resolution_summary,'[[:space:]]+',' ')),''),'No resolution recorded'))),CURRENT_TIMESTAMP
  FROM service_logs sl INNER JOIN equipment_units eu ON eu.equipment_id=sl.equipment_id
  ON DUPLICATE KEY UPDATE equipment_id=VALUES(equipment_id),payload_text=VALUES(payload_text),prepared_at=VALUES(prepared_at);
END$$
CREATE TRIGGER trg_service_logs_after_insert_audit AFTER INSERT ON service_logs FOR EACH ROW
BEGIN INSERT INTO audit_logs(database_user,table_name,record_id,operation_type,old_values,new_values,change_description) VALUES(CURRENT_USER(),'service_logs',NEW.dispatch_id,'INSERT',NULL,JSON_OBJECT('dispatch_id',NEW.dispatch_id,'equipment_id',NEW.equipment_id,'status',NEW.status),'New service log created'); END$$
CREATE TRIGGER trg_service_logs_before_update_audit BEFORE UPDATE ON service_logs FOR EACH ROW
BEGIN INSERT INTO audit_logs(database_user,table_name,record_id,operation_type,old_values,new_values,change_description) VALUES(CURRENT_USER(),'service_logs',OLD.dispatch_id,'UPDATE',JSON_OBJECT('dispatch_id',OLD.dispatch_id,'status',OLD.status),JSON_OBJECT('dispatch_id',NEW.dispatch_id,'status',NEW.status),'Service log changed; pre-change and post-change values captured'); END$$
CREATE TRIGGER trg_audit_logs_before_update_immutable BEFORE UPDATE ON audit_logs FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='audit_logs is append-only; updates are prohibited'; END$$
CREATE TRIGGER trg_audit_logs_before_delete_immutable BEFORE DELETE ON audit_logs FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='audit_logs is append-only; deletes are prohibited'; END$$
DELIMITER ;
CALL sp_Prepare_RAG_Payloads();
CREATE OR REPLACE SQL SECURITY DEFINER VIEW vw_service_rag_safe AS SELECT sl.dispatch_id,sl.equipment_id,eu.model AS equipment_model,eu.serial_number,sl.dispatch_date,sl.technician,sl.service_type,sl.resolution_summary,sl.status,sl.rag_service_context FROM service_logs sl INNER JOIN equipment_units eu ON eu.equipment_id=sl.equipment_id;
CREATE ROLE IF NOT EXISTS 'gigavolt_support_reader';
GRANT SELECT ON gigavolt_silver.vw_service_rag_safe TO 'gigavolt_support_reader';
SQL

echo 'Milestones 1 and 2 setup complete.'
mariadb --protocol=socket -e "SELECT 'service_logs' AS table_name,COUNT(*) AS row_count FROM gigavolt_silver.service_logs UNION ALL SELECT 'rag_service_payloads',COUNT(*) FROM gigavolt_silver.rag_service_payloads UNION ALL SELECT 'audit_logs',COUNT(*) FROM gigavolt_silver.audit_logs; SHOW TRIGGERS FROM gigavolt_silver;"
