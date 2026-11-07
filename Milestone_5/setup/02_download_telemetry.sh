#!/usr/bin/env bash
# Project Gigavolt | Milestone 5 | Download the instructor's IoT telemetry feed (raw / Bronze copy)
#
# Usage (from the Milestone_5 folder):  bash setup/02_download_telemetry.sh
# The raw file is never modified; the loader reads it and records its SHA-256 for provenance.
set -Eeuo pipefail

URL="${TELEMETRY_URL:-https://622.gomillion.org/data/telemetry_data.json}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW_DIR="$(dirname "$SCRIPT_DIR")/data/raw"
TARGET="$RAW_DIR/telemetry_data.json"

mkdir -p "$RAW_DIR"
wget --tries=3 --timeout=30 -O "$TARGET.tmp" "$URL"
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

echo "Saved $TARGET"
cat "$RAW_DIR/telemetry_manifest.json"
echo "First record:"
head -n 1 "$TARGET"
