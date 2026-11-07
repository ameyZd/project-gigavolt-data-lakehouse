#!/usr/bin/env bash
# Project Gigavolt | Milestone 5 | Single-instance MariaDB with the Milestone 1-2 ERP data
#
# Milestone 5 does NOT need the Milestone 3 Galera cluster: one MariaDB server on the same
# EC2 instance as MongoDB and ChromaDB is enough (and keeps every read on localhost).
#
#   sudo bash setup/00_install_mariadb_and_load_erp.sh            # install + load if gigavolt_silver is missing
#   sudo bash setup/00_install_mariadb_and_load_erp.sh --reload   # force a rebuild of bronze + silver
#
# The load itself reuses Milestone_3/bootstrap/bootstrap_milestones_1_2_standalone.sh, which
# downloads fall2026.zip, builds gigavolt_bronze -> cleans -> gigavolt_silver, and applies the
# Milestone 2 RAG column, payload table, security view and audit triggers.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOADER="$REPO_ROOT/Milestone_3/bootstrap/bootstrap_milestones_1_2_standalone.sh"

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

if ! command -v mariadb >/dev/null; then
  echo "1/3 Installing MariaDB server ..."
  apt-get update -y
  apt-get install -y mariadb-server mariadb-client
else
  echo "1/3 MariaDB already installed: $(mariadb --version)"
fi

echo "2/3 Starting MariaDB (auto-starts after an AWS restart) ..."
systemctl enable --now mariadb
for _ in $(seq 1 30); do mariadb -NBe "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done
mariadb -NBe "SELECT VERSION();"

if [[ ${1:-} == --reload ]] || ! mariadb -NBe "SELECT COUNT(*) FROM gigavolt_silver.equipment_units" >/dev/null 2>&1; then
  [[ -f "$LOADER" ]] || { echo "Missing $LOADER (clone the whole repo)." >&2; exit 1; }
  echo "3/3 Loading Milestone 1-2 data into gigavolt_silver ..."
  bash "$LOADER" --reset
else
  echo "3/3 gigavolt_silver already loaded (use --reload to rebuild)."
fi

mariadb -t -e "
SELECT 'equipment_units' AS table_name, COUNT(*) AS row_count FROM gigavolt_silver.equipment_units
UNION ALL SELECT 'service_logs',     COUNT(*) FROM gigavolt_silver.service_logs
UNION ALL SELECT 'warranty_claims',  COUNT(*) FROM gigavolt_silver.warranty_claims
UNION ALL SELECT 'parts_inventory',  COUNT(*) FROM gigavolt_silver.parts_inventory;"
echo "MariaDB is ready (single instance). Next: setup/01_install_mongodb.sh"
