import csv
import html
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

RAW_DIR = Path("/opt/data/gigavolt/raw")
CLEAN_DIR = Path("/opt/data/gigavolt/clean")
REJECT_DIR = CLEAN_DIR / "rejected"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)
REJECT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG = {
    "customers": {
        "file": "customers.csv",
        "pk": "customer_id",
        "dates": ["created_date"],
        "numeric": [],
    },
    "equipment": {
        "file": "equipment.csv",
        "pk": "equipment_id",
        "dates": ["install_date", "warranty_expiration"],
        "numeric": ["capacity_kw"],
    },
    "dispatches": {
        "file": "dispatches.csv",
        "pk": "dispatch_id",
        "dates": ["dispatch_date"],
        "numeric": ["hours_spent", "labor_cost"],
    },
    "parts": {
        "file": "parts.csv",
        "pk": "part_id",
        "dates": [],
        "numeric": ["unit_cost", "unit_price", "stock_quantity", "reorder_level"],
    },
    "warranty_claims": {
        "file": "warranty_claims.csv",
        "pk": "claim_id",
        "dates": ["claim_date"],
        "numeric": ["claim_amount", "approved_amount"],
    },
}

HTML_RE = re.compile(r"<[^>]*>")
SPACE_RE = re.compile(r"\s+")
NULL_MARKERS = {"", "NULL", "N/A", "NA"}

def clean_text(value):
    value = "" if value is None else str(value)
    value = unicodedata.normalize("NFKC", value)
    value = html.unescape(value)
    value = HTML_RE.sub("", value)
    value = SPACE_RE.sub(" ", value).strip()
    return "" if value.upper() in NULL_MARKERS else value

def clean_date(value):
    value = clean_text(value)
    if not value:
        return ""

    formats = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    raise ValueError(f"unrecognized date: {value}")

def clean_number(value):
    value = clean_text(value).replace("$", "").replace(",", "")
    if not value:
        return ""

    try:
        number = Decimal(value)
        return format(number.normalize(), "f")
    except InvalidOperation:
        raise ValueError(f"invalid numeric value: {value}")

def clean_phone(value):
    value = clean_text(value)
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return value

def normalize_country(value):
    value = clean_text(value).upper()
    if value in {"US", "USA", "U.S.", "UNITED STATES"}:
        return "US"
    return value

def clean_row(table_name, raw_row, source_row):
    config = CONFIG[table_name]
    row = {column: clean_text(value) for column, value in raw_row.items()}

    for column in config["dates"]:
        row[column] = clean_date(row[column])

    for column in config["numeric"]:
        row[column] = clean_number(row[column])

    if "status" in row and row["status"]:
        row["status"] = row["status"].title()

    if "state" in row and row["state"]:
        row["state"] = row["state"].upper()

    if "country" in row:
        row["country"] = normalize_country(row["country"])

    if "phone" in row:
        row["phone"] = clean_phone(row["phone"])

    row["_source_row"] = source_row
    return row

def completeness(row):
    return sum(bool(value) for key, value in row.items() if key != "_source_row")

def deduplicate(table_name, rows, rejected):
    pk = CONFIG[table_name]["pk"]
    groups = defaultdict(list)

    for row in rows:
        if not row[pk]:
            rejected.append((row, f"missing required primary key: {pk}"))
        else:
            groups[row[pk]].append(row)

    kept = []
    for key, group in groups.items():
        winner = max(group, key=completeness)
        kept.append(winner)

        for row in group:
            if row is not winner:
                rejected.append((row, f"duplicate {pk}: {key}"))

    return kept

def write_csv(path, rows, fieldnames):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})

def write_rejections(table_name, rejected, columns):
    rows = []
    for row, reason in rejected:
        output = {
            "source_row": row.get("_source_row", ""),
            "rejection_reason": reason,
        }
        output.update({column: row.get(column, "") for column in columns})
        rows.append(output)

    write_csv(
        REJECT_DIR / f"{table_name}_rejected.csv",
        rows,
        ["source_row", "rejection_reason"] + list(columns),
    )

cleaned = {}
rejections = {table: [] for table in CONFIG}
input_counts = {}

# Clean and deduplicate every source file.
for table_name, config in CONFIG.items():
    source_path = RAW_DIR / config["file"]

    with source_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as file:
        reader = csv.DictReader(file)
        source_rows = list(reader)

    input_counts[table_name] = len(source_rows)
    cleaned_rows = []

    for source_row, raw_row in enumerate(source_rows, start=2):
        try:
            cleaned_rows.append(clean_row(table_name, raw_row, source_row))
        except ValueError as error:
            rejected_row = {column: clean_text(value) for column, value in raw_row.items()}
            rejected_row["_source_row"] = source_row
            rejections[table_name].append((rejected_row, str(error)))

    cleaned[table_name] = deduplicate(table_name, cleaned_rows, rejections[table_name])

# Enforce parent-child relationships for the Silver output.
customer_ids = {row["customer_id"] for row in cleaned["customers"]}
equipment_ids = {row["equipment_id"] for row in cleaned["equipment"]}
dispatch_ids = {row["dispatch_id"] for row in cleaned["dispatches"]}
part_ids = {row["part_id"] for row in cleaned["parts"]}

def keep_rows(table_name, predicate, reason):
    kept = []
    for row in cleaned[table_name]:
        if predicate(row):
            kept.append(row)
        else:
            rejections[table_name].append((row, reason(row)))
    cleaned[table_name] = kept

keep_rows(
    "equipment",
    lambda row: row["customer_id"] in customer_ids,
    lambda row: f"customer_id not found after cleansing: {row['customer_id']}",
)

equipment_ids = {row["equipment_id"] for row in cleaned["equipment"]}

keep_rows(
    "dispatches",
    lambda row: row["equipment_id"] in equipment_ids and row["customer_id"] in customer_ids,
    lambda row: "equipment_id or customer_id not found after cleansing",
)

dispatch_ids = {row["dispatch_id"] for row in cleaned["dispatches"]}

keep_rows(
    "warranty_claims",
    lambda row: (
        row["equipment_id"] in equipment_ids
        and (not row["dispatch_id"] or row["dispatch_id"] in dispatch_ids)
        and (not row["part_id"] or row["part_id"] in part_ids)
    ),
    lambda row: "equipment_id, dispatch_id, or part_id not found after cleansing",
)

# Write clean files, rejection logs, and a summary report.
summary = []
for table_name, config in CONFIG.items():
    columns = list(cleaned[table_name][0].keys()) if cleaned[table_name] else []
    columns = [column for column in columns if column != "_source_row"]

    write_csv(CLEAN_DIR / f"{table_name}_clean.csv", cleaned[table_name], columns)
    write_rejections(table_name, rejections[table_name], columns)

    summary.append(
        f"{table_name}: input={input_counts[table_name]}, "
        f"clean={len(cleaned[table_name])}, "
        f"rejected={len(rejections[table_name])}"
    )

summary_path = CLEAN_DIR / "cleaning_summary.txt"
summary_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

print("Cleansing complete.")
for line in summary:
    print(line)

print(f"\nClean files: {CLEAN_DIR}")
print(f"Rejected rows: {REJECT_DIR}")
print(f"Summary report: {summary_path}")