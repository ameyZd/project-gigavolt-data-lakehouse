# Project Gigavolt — Data Lakehouse & AI-Ready Operations

> **ISTM 622 · Advanced Data Management · Fall 2026 · Author: Amey Dhote**

Project Gigavolt transforms raw ERP extracts into a clean, normalized MariaDB data platform. The project follows a Bronze → Silver lakehouse pattern, applies documented data-quality rules, validates referential integrity, and extends the service layer with RAG-ready payloads, immutable audit logging, and least-privilege access.

## Highlights

- Ingests five raw ERP datasets into a lossless **Bronze** layer.
- Cleans dates, HTML, encoding artifacts, numeric fields, duplicate records, and invalid relationships.
- Loads a normalized **3NF Silver** schema with primary and foreign keys.
- Produces auditable data-quality and integrity checks.
- Creates AI-ready service context and provenance-preserving RAG payloads.
- Captures `INSERT` and `UPDATE` activity in an append-only audit table.
- Restricts support/RAG consumers to a privacy-conscious security view.

## Architecture

```text
Raw CSV extracts
     │
     ▼
Bronze: gigavolt_bronze
  • Raw, text-preserving staging tables
     │
     ▼
Python cleansing and quarantine
  • Normalization, validation, de-duplication, FK checks
     │
     ▼
Silver: gigavolt_silver
  • 3NF operational schema with enforced relationships
     │
     ├── RAG context + prepared payloads
     ├── Immutable audit trail
     └── Restricted support/RAG view
```

## Data Model

The Silver layer uses five related entities:

| Table | Purpose | Key relationship |
| --- | --- | --- |
| `customers` | Customer account and contact data | One customer → many equipment units |
| `equipment_units` | Installed Gigavolt equipment | Many units → one customer |
| `service_logs` | Field-service dispatch records | Many logs → one equipment unit |
| `parts_inventory` | Parts catalog and inventory | Referenced by warranty claims |
| `warranty_claims` | Warranty claim activity | References equipment; optionally service logs and parts |

The ERD source is available at [`Milestone_1/gigavolt/scripts/05_erd/gigavolt_erd.dbml`](Milestone_1/gigavolt/scripts/05_erd/gigavolt_erd.dbml).

## Repository Layout

```text
.
├── Milestone_1/
│   ├── bootstrap_gigavolt_m1.sh         # Fresh Ubuntu/MariaDB recovery script
│   ├── gigavolt/
│   │   ├── raw/                         # Source ERP CSV extracts
│   │   └── scripts/
│   │       ├── 01_bronze/               # Bronze DDL, loading, validation
│   │       ├── 02_profiling/            # Data profiling utilities
│   │       ├── 03_cleaning/             # Python cleansing pipeline
│   │       ├── 04_silver/               # 3NF DDL, loading, validation
│   │       └── 05_erd/                  # DBML ERD source
│   └── script.md                       # Milestone 2 presentation narration
└── Milestone_2/
    └── gigavolt/scripts/06_milestone_2/
        ├── 01_rag_generated_column.sql
        ├── 02_prepare_rag_payloads.sql
        ├── 03_audit_logs_and_triggers.sql
        └── 04_security_view.sql
```

## Milestone 1 — Bronze to Silver Lakehouse

### 1. Create the Bronze layer

Run [`01_schema_and_load.sql`](Milestone_1/gigavolt/scripts/01_bronze/01_schema_and_load.sql) to create `gigavolt_bronze` and load the raw ERP files. Bronze tables intentionally retain raw values as text so the original extracts remain traceable.

### 2. Profile and cleanse the data

The cleansing pipeline in [`01_data_cleaning.py`](Milestone_1/gigavolt/scripts/03_cleaning/01_data_cleaning.py) handles:

- HTML decoding and tag removal using `html.unescape()` and the regex `<[^>]*>`
- Unicode normalization (`NFKC`) and whitespace cleanup
- Multiple date formats standardized to `YYYY-MM-DD`
- Currency and numeric normalization
- Phone, state, country, and status standardization
- Duplicate-key resolution using the most complete record
- Invalid primary keys and orphaned relationships sent to quarantine files

### 3. Create and load the Silver layer

Run [`01_schema.sql`](Milestone_1/gigavolt/scripts/04_silver/01_schema.sql), then [`02_load.sql`](Milestone_1/gigavolt/scripts/04_silver/02_load.sql). The Silver schema stores typed, normalized data in InnoDB tables with enforced foreign keys.

### 4. Validate results

Run [`03_validation.sql`](Milestone_1/gigavolt/scripts/04_silver/03_validation.sql). The validation checks row-count reconciliation, foreign-key orphans, residual HTML, and invalid dates.

| Dataset | Raw rows | Clean/Silver rows | Quarantined |
| --- | ---: | ---: | ---: |
| Customers | 525 | 500 | 25 |
| Equipment | 2,575 | 2,500 | 75 |
| Service logs | 5,000 | 4,864 | 136 |
| Parts | 150 | 150 | 0 |
| Warranty claims | 1,248 | 1,171 | 77 |

All required foreign-key validation queries returned **zero orphan rows**.

### Fresh-instance bootstrap

For an Ubuntu EC2 instance, paste the contents of [`bootstrap_gigavolt_m1.sh`](Milestone_1/bootstrap_gigavolt_m1.sh) into EC2 **User data**, or run it after launch:

```bash
sudo bash bootstrap_gigavolt_m1.sh
sudo mariadb gigavolt_silver
```

> The bootstrap deliberately drops and recreates only `gigavolt_bronze` and `gigavolt_silver`.

## Milestone 2 — AI-Ready, Auditable Service Data

Milestone 2 builds on the Silver layer with MariaDB programming constructs designed for future retrieval-augmented generation (RAG), operational traceability, and secure data access.

### Generated service context

[`01_rag_generated_column.sql`](Milestone_2/gigavolt/scripts/06_milestone_2/01_rag_generated_column.sql) adds `rag_service_context`, a **virtual generated column** on `service_logs`. MariaDB dynamically combines dispatch, equipment, service date, technician, type, resolution, and status into readable context without storing redundant text.

### RAG payload procedure

[`02_prepare_rag_payloads.sql`](Milestone_2/gigavolt/scripts/06_milestone_2/02_prepare_rag_payloads.sql) creates the `rag_service_payloads` table and `sp_Prepare_RAG_Payloads()` procedure. It:

- Joins service logs to equipment data.
- Produces consistent, readable payload text.
- Preserves source table and source record ID for provenance.
- Uses a transaction and exception handler for safe execution.
- Upserts by dispatch ID so reruns remain idempotent.

### Immutable audit logging

[`03_audit_logs_and_triggers.sql`](Milestone_2/gigavolt/scripts/06_milestone_2/03_audit_logs_and_triggers.sql) creates append-only `audit_logs` and triggers for `INSERT` and `UPDATE` events on `service_logs`. Each audit entry captures timestamp, database user, operation, record ID, old/new JSON snapshots, and a readable change description. Update and delete attempts against audit rows are rejected.

### Secure reader view

[`04_security_view.sql`](Milestone_2/gigavolt/scripts/06_milestone_2/04_security_view.sql) provides `vw_service_rag_safe` and the `gigavolt_support_reader` role. The view exposes operational service and equipment context while excluding customer identities, contact information, addresses, warranty claims, and financial fields. The role receives `SELECT` access only to this view.

### Run Milestone 2 scripts

With Milestone 1 successfully loaded, connect to MariaDB with an account that can alter tables, create routines/triggers, and grant privileges. Run the scripts in order:

```sql
SOURCE Milestone_2/gigavolt/scripts/06_milestone_2/01_rag_generated_column.sql;
SOURCE Milestone_2/gigavolt/scripts/06_milestone_2/02_prepare_rag_payloads.sql;
SOURCE Milestone_2/gigavolt/scripts/06_milestone_2/03_audit_logs_and_triggers.sql;
SOURCE Milestone_2/gigavolt/scripts/06_milestone_2/04_security_view.sql;
```

## Demonstrations & Documentation

- Milestone 1 video: [YouTube demonstration](https://youtu.be/bZRd8IKO9S4)
- Milestone 2 presentation narration: [`script.md`](Milestone_1/script.md)

## Technology Stack

| Technology | Use |
| --- | --- |
| MariaDB | Bronze/Silver storage, constraints, routines, views, roles, and triggers |
| Python 3 | Repeatable CSV cleansing, validation, and quarantine output |
| SQL | Schema definition, loading, profiling, data-quality, and integrity checks |
| DBML | Entity relationship diagram source |
| Ubuntu / EC2 | Reproducible database environment |

## AI Use Statement

AI was used as a support tool for repetitive tasks such as organizing SQL script structure, improving comments, and drafting documentation. Database implementation, testing, and validation were reviewed and executed by the author. Generated suggestions were checked against MariaDB syntax and verified through the project’s output and validation queries.

---

Built for **Project Gigavolt**, ISTM 622 Advanced Data Management.
