import csv
import re
from collections import Counter
from pathlib import Path

RAW_DIR = Path("/opt/data/gigavolt/raw")

FILES = {
    "customers": ("customers.csv", "customer_id"),
    "equipment": ("equipment.csv", "equipment_id"),
    "dispatches": ("dispatches.csv", "dispatch_id"),
    "parts": ("parts.csv", "part_id"),
    "warranty_claims": ("warranty_claims.csv", "claim_id"),
}

HTML_RE = re.compile(r"<[^>]*>")
MULTI_SPACE_RE = re.compile(r"\s{2,}")

def is_missing(value):
    return value.strip().upper() in {"", "NULL", "N/A", "NA"}

def date_format(value):
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        return "ISO timestamp"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return "YYYY-MM-DD"
    if re.fullmatch(r"\d{4}/\d{2}/\d{2}", value):
        return "YYYY/MM/DD"
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
        return "MM/DD/YYYY"
    if re.fullmatch(r"\d{2}-\d{2}-\d{4}", value):
        return "MM-DD-YYYY"
    return "Other"

for table_name, (filename, primary_key) in FILES.items():
    path = RAW_DIR / filename

    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as file:
        rows = list(csv.DictReader(file))

    columns = rows[0].keys() if rows else []
    primary_keys = Counter(row.get(primary_key, "").strip() for row in rows)

    print("\n" + "=" * 72)
    print(f"TABLE: {table_name} | SOURCE FILE: {filename}")
    print(f"Raw row count: {len(rows)}")
    print("-" * 72)

    duplicate_groups = sum(1 for key, count in primary_keys.items()
                           if key and count > 1)
    duplicate_extra_rows = sum(count - 1 for key, count in primary_keys.items()
                               if key and count > 1)

    print(f"Duplicate {primary_key} groups: {duplicate_groups}")
    print(f"Extra duplicate rows: {duplicate_extra_rows}")

    for column in columns:
        values = [str(row.get(column, "") or "") for row in rows]
        missing = sum(is_missing(value) for value in values)
        html = sum(bool(HTML_RE.search(value)) for value in values)
        whitespace = sum(
            value != value.strip() or bool(MULTI_SPACE_RE.search(value))
            for value in values if not is_missing(value)
        )
        encoding = sum(
            any(marker in value for marker in ("�", "Ã", "â"))
            for value in values
        )

        if missing or html or whitespace or encoding:
            print(
                f"{column}: missing={missing}, html={html}, "
                f"whitespace={whitespace}, encoding_flags={encoding}"
            )

        if "date" in column.lower() or "expiration" in column.lower():
            formats = Counter(
                date_format(value)
                for value in values
                if not is_missing(value)
            )
            print(f"{column} date formats: {dict(formats)}")

        if column.lower() == "status":
            statuses = Counter(
                value.strip()
                for value in values
                if not is_missing(value)
            )
            print(f"{column} values: {dict(statuses)}")

        if column.lower() in {
            "labor_cost", "unit_cost", "unit_price",
            "claim_amount", "approved_amount"
        }:
            currency_symbols = sum("$" in value for value in values)
            print(f"{column} values containing $: {currency_symbols}")