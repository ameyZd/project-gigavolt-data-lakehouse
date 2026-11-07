#!/usr/bin/env bash
# Project Gigavolt | Milestone 5 | Verify everything is wired up (normal user, NOT sudo)
#
#   bash setup/06_verify.sh
#
# Runs the offline tests, then the live health check of MariaDB, MongoDB, ChromaDB and the LLM.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M5_DIR="$(dirname "$SCRIPT_DIR")"
PY="$M5_DIR/.venv/bin/python"
cd "$M5_DIR"

echo "== Offline tests"
"$PY" -m unittest tests/test_offline.py 2>&1 | tail -n 3

echo; echo "== Live health check"
if grep -qE '^LLM_PROVIDER=extractive' .env 2>/dev/null; then
  "$PY" scripts/03_check_sources.py
else
  "$PY" scripts/03_check_sources.py --llm
fi
