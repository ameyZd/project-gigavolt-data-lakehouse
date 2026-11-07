#!/usr/bin/env bash
# Milestone 5 one-command setup for a fresh Ubuntu EC2 instance (t3.medium, 20 GB).
# Run as the normal ubuntu user from inside the cloned repo:
#     bash Milestone_5/setup.sh
# Safe to re-run: every step skips or overwrites work that is already done.
set -Eeuo pipefail
cd "$(dirname "$0")"
REPO="$(cd .. && pwd)"
log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
export DEBIAN_FRONTEND=noninteractive

log "1/7 System packages (Docker for MongoDB, unzip, curl)"
sudo apt-get update -y
sudo -E apt-get install -y docker.io unzip curl
sudo -E apt-get install -y docker-compose-v2 || echo "(compose plugin not available - will use plain docker run)"
sudo systemctl enable --now docker

log "2/7 MariaDB + ERP Silver layer (re-uses the Milestone 1 bootstrap)"
if sudo mariadb -e 'SELECT 1 FROM gigavolt_silver.equipment_units LIMIT 1' >/dev/null 2>&1; then
  echo "gigavolt_silver already loaded - skipping"
else
  sudo bash "$REPO/Milestone_1/bootstrap_gigavolt_m1.sh"
fi
sudo mariadb < sql/rag_setup.sql            # RAG indexes + read-only rag_reader user

log "3/7 MongoDB 7 document store (Docker, data on a persistent volume)"
if sudo docker ps -a --format '{{.Names}}' | grep -qx gigavolt-mongo; then
  sudo docker start gigavolt-mongo >/dev/null
elif sudo docker compose version >/dev/null 2>&1; then
  sudo docker compose -f config/docker-compose.yml up -d
else
  sudo docker run -d --name gigavolt-mongo --restart unless-stopped -p 127.0.0.1:27017:27017 \
       -v mongo_data:/data/db mongo:7.0 --wiredTigerCacheSizeGB 0.5
fi
for i in $(seq 1 60); do
  sudo docker exec gigavolt-mongo mongosh --quiet --eval 'db.adminCommand("ping").ok' 2>/dev/null | grep -q 1 && break
  [ "$i" = 60 ] && { echo "MongoDB did not start - check: sudo docker logs gigavolt-mongo"; exit 1; }
  sleep 2
done
echo "MongoDB is up"

log "4/7 Download data (IoT telemetry JSON + technical PDFs)"
mkdir -p data/pdfs
[ -s data/telemetry_data.json ] || curl -fsSL --retry 3 https://622.gomillion.org/data/telemetry_data.json -o data/telemetry_data.json
[ -s data/UnstructuredData.zip ] || curl -fsSL --retry 3 https://622.gomillion.org/data/UnstructuredData.zip -o data/UnstructuredData.zip
unzip -oq data/UnstructuredData.zip -d data/pdfs
echo "telemetry lines: $(wc -l < data/telemetry_data.json), PDFs: $(find data/pdfs -name '*.pdf' | wc -l)"

log "5/7 Python 3.12 virtual env via uv (fast, independent of the OS Python)"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
[ -x .venv/bin/python ] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

log "6/7 Config"
[ -f .env ] || cp .env.example .env

log "7/7 Load telemetry into MongoDB and build the ChromaDB vector store"
.venv/bin/python load_telemetry.py
.venv/bin/python build_vector_store.py

cat <<'DONE'

Setup complete. Next:
  1) nano Milestone_5/.env            -> paste your free Groq key into LLM_API_KEY
  2) cd Milestone_5 && source .venv/bin/activate
  3) python rag.py "Which suppliers provide lithium cells for our commercial energy storage units?"
  4) python benchmark.py              -> results/benchmark.md + results/test_query_results.md
DONE
