#!/usr/bin/env bash
# Project Gigavolt | Milestone 5 | Load the document store and the vector store (normal user, NOT sudo)
#
#   bash setup/04_load_data.sh
#
#   1. Download the instructor's IoT telemetry feed -> data/raw/telemetry_data.json (+ sha256 manifest)
#   2. Load it into MongoDB gigavolt_iot.bms_telemetry (validator, nested docs, 8 indexes)
#   3. Build the ChromaDB vector store from the 25 technical PDFs -> data/chroma_db (101 vectors)
# Needs: setup/02 (MongoDB running) and setup/03 (.venv).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M5_DIR="$(dirname "$SCRIPT_DIR")"
PY="$M5_DIR/.venv/bin/python"
URL="${TELEMETRY_URL:-https://622.gomillion.org/data/telemetry_data.json}"
RAW_DIR="$M5_DIR/data/raw"
TARGET="$RAW_DIR/telemetry_data.json"

[[ $EUID -ne 0 ]] || { echo "Run as your normal user (no sudo)." >&2; exit 1; }
[[ -x "$PY" ]] || { echo "Missing $PY. Run: bash setup/03_python_env.sh" >&2; exit 1; }

echo "== 1/3 Download telemetry (raw / Bronze copy)"
mkdir -p "$RAW_DIR"
if command -v wget >/dev/null; then wget -q --tries=3 --timeout=30 -O "$TARGET.tmp" "$URL"
else curl -fsS --retry 3 --max-time 60 -o "$TARGET.tmp" "$URL"; fi
mv "$TARGET.tmp" "$TARGET"
LINES=$(grep -c '[^[:space:]]' "$TARGET" || true)
SHA=$(sha256sum "$TARGET" | awk '{print $1}')
cat > "$RAW_DIR/telemetry_manifest.json" <<JSON
{
  "source_url": "$URL",
  "downloaded_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "file": "telemetry_data.json",
  "bytes": $(stat -c %s "$TARGET"),
  "non_empty_lines": $LINES,
  "sha256": "$SHA"
}
JSON
cat "$RAW_DIR/telemetry_manifest.json"

echo; echo "== 2/3 Load telemetry into MongoDB"
cd "$M5_DIR"
"$PY" scripts/01_load_telemetry_mongodb.py --drop

echo; echo "== 3/3 Build the vector store (technical PDFs -> ChromaDB)"
"$PY" scripts/00_build_vector_store.py --rebuild
