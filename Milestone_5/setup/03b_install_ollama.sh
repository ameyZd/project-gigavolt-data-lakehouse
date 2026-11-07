#!/usr/bin/env bash
# Project Gigavolt | Milestone 5 | Free local LLM with Ollama (only used when LLM_PROVIDER=ollama)
#
#   sudo bash setup/03b_install_ollama.sh             # model from .env OLLAMA_MODEL (default llama3.2:3b)
#
# Installs Ollama with its official installer (https://ollama.com), enables the systemd service
# (127.0.0.1:11434 only), pulls the model, and times one tiny test prompt.
# No account and no API key; everything runs on this EC2 instance.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
MODEL="$(grep -E '^OLLAMA_MODEL=' "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2-)"
MODEL="${MODEL:-llama3.2:3b}"

if ! command -v ollama >/dev/null; then
  echo "Installing Ollama ..."
  apt-get install -y curl zstd >/dev/null 2>&1 || true
  curl -fsSL https://ollama.com/install.sh | sh
fi
systemctl enable --now ollama
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:11434/api/version; echo

echo "Pulling $MODEL (about 2 GB the first time) ..."
ollama pull "$MODEL"
ollama list

echo "Test prompt (first call loads the model into RAM):"
START=$(date +%s.%N)
curl -fsS http://127.0.0.1:11434/api/generate \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"Reply with the single word OK.\",\"stream\":false,\"keep_alive\":\"60m\"}" \
  | python3 -c 'import json,sys; print("  reply:", json.load(sys.stdin).get("response","").strip()[:40])'
echo "  took $(python3 -c "print(round($(date +%s.%N) - $START, 1))") s"
echo "Ollama ready: $MODEL on 127.0.0.1:11434"
