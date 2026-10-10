#!/usr/bin/env bash
# Project Gigavolt | Milestone 1 recovery bootstrap for a new Ubuntu EC2 instance.
# Run as EC2 User Data or: sudo bash bootstrap_gigavolt_m1.sh
# WARNING: Drops and recreates only gigavolt_bronze and gigavolt_silver.
set -Eeuo pipefail
DATA_ROOT=/opt/data/gigavolt; RAW_DIR="$DATA_ROOT/raw"; CLEAN_DIR="$DATA_ROOT/clean"
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y mariadb-server python3 curl unzip
systemctl enable --now mariadb
install -d -m 0755 "$RAW_DIR" "$CLEAN_DIR/rejected"
curl --fail --location --retry 3 'https://622.gomillion.org/data/fall2026.zip' -o "$RAW_DIR/fall2026.zip"
unzip -o "$RAW_DIR/fall2026.zip" -d "$RAW_DIR"
for f in customers.csv equipment.csv dispatches.csv parts.csv warranty_claims.csv; do test -f "$RAW_DIR/$f" || { echo "Missing $f after download." >&2; exit 1; }; done

# Bronze: retain every source value as text before cleansing.
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

# Clean dates, numeric fields, text/HTML, duplicate keys, and orphan relationships.
RAW_DIR="$RAW_DIR" CLEAN_DIR="$CLEAN_DIR" python3 - <<'PY'
import csv, html, os, re, unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
raw, clean=Path(os.environ['RAW_DIR']),Path(os.environ['CLEAN_DIR']); reject=clean/'rejected'; reject.mkdir(exist_ok=True)
cfg={'customers':('customers.csv','customer_id',['created_date'],[]),'equipment':('equipment.csv','equipment_id',['install_date','warranty_expiration'],['capacity_kw']),'dispatches':('dispatches.csv','dispatch_id',['dispatch_date'],['hours_spent','labor_cost']),'parts':('parts.csv','part_id',[],['unit_cost','unit_price','stock_quantity','reorder_level']),'warranty_claims':('warranty_claims.csv','claim_id',['claim_date'],['claim_amount','approved_amount'])}
html_re=re.compile(r'<[^>]*>'); space=re.compile(r'\s+'); nulls={'','NULL','N/A','NA'}
def tx(x):
 x=space.sub(' ',html_re.sub('',unicodedata.normalize('NFKC',html.unescape('' if x is None else str(x))))).strip(); return '' if x.upper() in nulls else x
def dt(x):
 x=tx(x)
 if not x:return ''
 for f in ('%Y-%m-%dT%H:%M:%SZ','%Y-%m-%d','%Y/%m/%d','%m/%d/%Y','%m-%d-%Y'):
  try:return datetime.strptime(x,f).strftime('%Y-%m-%d')
  except ValueError:pass
 raise ValueError('invalid date')
def num(x):
 x=tx(x).replace('$','').replace(',',''); return '' if not x else format(Decimal(x).normalize(),'f')
def out(path,rows,fields):
 with path.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({k:r.get(k,'') for k in fields} for r in rows)
data={}; bad=defaultdict(list); totals={}
for name,(file,pk,dates,nums) in cfg.items():
 with (raw/file).open(encoding='utf-8-sig',errors='replace',newline='') as f: rows=list(csv.DictReader(f))
 totals[name]=len(rows); groups=defaultdict(list)
 for i,source in enumerate(rows,2):
  try:
   r={k:tx(v) for k,v in source.items()}; [r.__setitem__(k,dt(r[k])) for k in dates]; [r.__setitem__(k,num(r[k])) for k in nums]
   if r.get('status'):r['status']=r['status'].title()
   if r.get('state'):r['state']=r['state'].upper()
   if 'country' in r:r['country']={'USA':'US','U.S.':'US','UNITED STATES':'US'}.get(r['country'].upper(),r['country'].upper())
   if r.get('phone'):
    d=re.sub(r'\D','',r['phone']);r['phone']=f'{d[:3]}-{d[3:6]}-{d[6:]}' if len(d)==10 else r['phone']
   r['_source_row']=i
   if not r[pk]:raise ValueError('missing primary key')
   groups[r[pk]].append(r)
  except Exception as e: bad[name].append((dict(source,_source_row=i),str(e)))
 data[name]=[]
 for key,dupes in groups.items():
  keep=max(dupes,key=lambda r:sum(bool(v) for k,v in r.items() if k!='_source_row'));data[name].append(keep)
  bad[name]+=[(r,f'duplicate {pk}: {key}') for r in dupes if r is not keep]
def filter_rows(name,pred,msg):
 good=[]
 for r in data[name]:
  if pred(r):good.append(r)
  else:bad[name].append((r,msg))
 data[name]=good
customers={r['customer_id'] for r in data['customers']};filter_rows('equipment',lambda r:r['customer_id'] in customers,'customer_id not found')
equipment={r['equipment_id'] for r in data['equipment']};filter_rows('dispatches',lambda r:r['equipment_id'] in equipment and r['customer_id'] in customers,'equipment_id or customer_id not found')
dispatches={r['dispatch_id'] for r in data['dispatches']};parts={r['part_id'] for r in data['parts']};filter_rows('warranty_claims',lambda r:r['equipment_id'] in equipment and (not r['dispatch_id'] or r['dispatch_id'] in dispatches) and (not r['part_id'] or r['part_id'] in parts),'foreign key not found')
for name in cfg:
 fields=[k for k in data[name][0] if k!='_source_row'];out(clean/f'{name}_clean.csv',data[name],fields)
 rejected=[dict(r,source_row=r.get('_source_row',''),rejection_reason=m) for r,m in bad[name]];out(reject/f'{name}_rejected.csv',rejected,['source_row','rejection_reason']+fields)
(clean/'cleaning_summary.txt').write_text('\n'.join(f'{n}: input={totals[n]}, clean={len(data[n])}, rejected={len(bad[n])}' for n in cfg)+'\n')
PY

# Silver: normalized 3NF tables and cleaned-file migration.
mariadb --protocol=socket --local-infile=1 <<SQL
DROP DATABASE IF EXISTS gigavolt_silver;
CREATE DATABASE gigavolt_silver CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE gigavolt_silver;
CREATE TABLE customers(customer_id VARCHAR(20) PRIMARY KEY,company_name VARCHAR(255) NOT NULL,contact_name VARCHAR(255) NOT NULL,phone VARCHAR(30),email VARCHAR(255),address VARCHAR(500),city VARCHAR(100),state CHAR(2),zip VARCHAR(20),country CHAR(2),created_date DATE,status VARCHAR(30)) ENGINE=InnoDB;
CREATE TABLE equipment_units(equipment_id VARCHAR(20) PRIMARY KEY,customer_id VARCHAR(20) NOT NULL,model VARCHAR(100),serial_number VARCHAR(100),install_date DATE,capacity_kw DECIMAL(12,2),voltage VARCHAR(30),warranty_expiration DATE,status VARCHAR(30),location_notes TEXT,FOREIGN KEY(customer_id) REFERENCES customers(customer_id)) ENGINE=InnoDB;
CREATE TABLE parts_inventory(part_id VARCHAR(20) PRIMARY KEY,part_name VARCHAR(255) NOT NULL,category VARCHAR(100),manufacturer VARCHAR(150),supplier VARCHAR(150),unit_cost DECIMAL(12,2),unit_price DECIMAL(12,2),stock_quantity INT,reorder_level INT) ENGINE=InnoDB;
CREATE TABLE service_logs(dispatch_id VARCHAR(20) PRIMARY KEY,equipment_id VARCHAR(20) NOT NULL,dispatch_date DATE,technician VARCHAR(255),service_type VARCHAR(100),hours_spent DECIMAL(10,2),labor_cost DECIMAL(12,2),resolution_summary TEXT,status VARCHAR(30),FOREIGN KEY(equipment_id) REFERENCES equipment_units(equipment_id)) ENGINE=InnoDB;
CREATE TABLE warranty_claims(claim_id VARCHAR(20) PRIMARY KEY,equipment_id VARCHAR(20) NOT NULL,dispatch_id VARCHAR(20),part_id VARCHAR(20),claim_date DATE,claim_amount DECIMAL(12,2),approved_amount DECIMAL(12,2),status VARCHAR(30),denial_reason TEXT,claim_notes TEXT,FOREIGN KEY(equipment_id) REFERENCES equipment_units(equipment_id),FOREIGN KEY(dispatch_id) REFERENCES service_logs(dispatch_id),FOREIGN KEY(part_id) REFERENCES parts_inventory(part_id)) ENGINE=InnoDB;
LOAD DATA LOCAL INFILE '$CLEAN_DIR/customers_clean.csv' INTO TABLE customers FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\r\n' IGNORE 1 LINES;
LOAD DATA LOCAL INFILE '$CLEAN_DIR/equipment_clean.csv' INTO TABLE equipment_units FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\r\n' IGNORE 1 LINES;
LOAD DATA LOCAL INFILE '$CLEAN_DIR/parts_clean.csv' INTO TABLE parts_inventory FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\r\n' IGNORE 1 LINES;
LOAD DATA LOCAL INFILE '$CLEAN_DIR/dispatches_clean.csv' INTO TABLE service_logs FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\r\n' IGNORE 1 LINES (@id,@equipment,@ignored_customer,@date,@technician,@type,@hours,@cost,@resolution,@status) SET dispatch_id=NULLIF(@id,''),equipment_id=NULLIF(@equipment,''),dispatch_date=NULLIF(@date,''),technician=NULLIF(@technician,''),service_type=NULLIF(@type,''),hours_spent=NULLIF(@hours,''),labor_cost=NULLIF(@cost,''),resolution_summary=NULLIF(@resolution,''),status=NULLIF(@status,'');
LOAD DATA LOCAL INFILE '$CLEAN_DIR/warranty_claims_clean.csv' INTO TABLE warranty_claims FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\r\n' IGNORE 1 LINES;
SELECT 'customers' AS table_name,COUNT(*) AS row_count FROM customers UNION ALL SELECT 'equipment_units',COUNT(*) FROM equipment_units UNION ALL SELECT 'service_logs',COUNT(*) FROM service_logs UNION ALL SELECT 'parts_inventory',COUNT(*) FROM parts_inventory UNION ALL SELECT 'warranty_claims',COUNT(*) FROM warranty_claims;
SELECT 'equipment_units -> customers' AS relationship,COUNT(*) AS orphan_rows FROM equipment_units e LEFT JOIN customers c ON c.customer_id=e.customer_id WHERE c.customer_id IS NULL UNION ALL SELECT 'service_logs -> equipment_units',COUNT(*) FROM service_logs s LEFT JOIN equipment_units e ON e.equipment_id=s.equipment_id WHERE e.equipment_id IS NULL UNION ALL SELECT 'warranty_claims -> equipment_units',COUNT(*) FROM warranty_claims w LEFT JOIN equipment_units e ON e.equipment_id=w.equipment_id WHERE e.equipment_id IS NULL;
SQL
echo 'Milestone 1 recovery is complete. Connect with: sudo mariadb gigavolt_silver'
