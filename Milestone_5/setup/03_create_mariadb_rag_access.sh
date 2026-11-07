#!/usr/bin/env bash
# Project Gigavolt | Milestone 5 | Create the RAG views and a SELECT-only MariaDB account
#
# Run on the EC2 instance AFTER MariaDB is running with gigavolt_silver loaded
# (setup/00_install_mariadb_and_load_erp.sh) and AFTER you copied .env.example to .env
# and set MARIADB_PASSWORD:
#   sudo bash setup/03_create_mariadb_rag_access.sh
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M5_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$M5_DIR/.env"

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE. Run: cp .env.example .env  and edit it." >&2; exit 1; }

read_env() { grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -e 's/[[:space:]]#.*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"; }
RAG_USER="$(read_env MARIADB_USER)";     RAG_USER="${RAG_USER:-gigavolt_rag}"
RAG_PASS="$(read_env MARIADB_PASSWORD)"
DB_NAME="$(read_env MARIADB_DATABASE)";  DB_NAME="${DB_NAME:-gigavolt_silver}"

if [[ -z "$RAG_PASS" || "$RAG_PASS" == "change-me" ]]; then
  echo "Set a real MARIADB_PASSWORD in $ENV_FILE first." >&2; exit 1
fi
[[ "$RAG_USER" =~ ^[A-Za-z0-9_]+$ ]] || { echo "MARIADB_USER may contain only letters, digits, underscore." >&2; exit 1; }
SQL_PASS="${RAG_PASS//\\/\\\\}"; SQL_PASS="${SQL_PASS//\'/\\\'}"

mariadb -NBe "SELECT VERSION();" || { echo "MariaDB is not reachable. Run: sudo systemctl start mariadb" >&2; exit 1; }
mariadb -NBe "SELECT COUNT(*) FROM ${DB_NAME}.equipment_units;" >/dev/null 2>&1 || {
  echo "${DB_NAME} is missing. Run: sudo bash setup/00_install_mariadb_and_load_erp.sh" >&2; exit 1; }

echo "1/2 Creating RAG views and supporting indexes ..."
mariadb < "$M5_DIR/sql/01_rag_views_and_indexes.sql"

echo "2/2 Creating SELECT-only account '$RAG_USER' ..."
mariadb <<SQL
CREATE USER IF NOT EXISTS '${RAG_USER}'@'localhost' IDENTIFIED BY '${SQL_PASS}';
CREATE USER IF NOT EXISTS '${RAG_USER}'@'127.0.0.1' IDENTIFIED BY '${SQL_PASS}';
ALTER USER '${RAG_USER}'@'localhost' IDENTIFIED BY '${SQL_PASS}';
ALTER USER '${RAG_USER}'@'127.0.0.1' IDENTIFIED BY '${SQL_PASS}';
GRANT SELECT ON ${DB_NAME}.vw_rag_equipment        TO '${RAG_USER}'@'localhost', '${RAG_USER}'@'127.0.0.1';
GRANT SELECT ON ${DB_NAME}.vw_rag_service_history  TO '${RAG_USER}'@'localhost', '${RAG_USER}'@'127.0.0.1';
GRANT SELECT ON ${DB_NAME}.vw_rag_warranty_claims  TO '${RAG_USER}'@'localhost', '${RAG_USER}'@'127.0.0.1';
GRANT SELECT ON ${DB_NAME}.vw_rag_parts_suppliers  TO '${RAG_USER}'@'localhost', '${RAG_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SHOW GRANTS FOR '${RAG_USER}'@'localhost';
SQL

echo "Done. The RAG pipeline can read only the four vw_rag_* views."
