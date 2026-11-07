#!/usr/bin/env bash
# Project Gigavolt | Milestone 5 | Python virtual environment in Milestone_5/.venv (normal user, NOT sudo)
#
#   bash setup/03_python_env.sh
#
# Installs the CPU-only PyTorch build first (avoids several GB of CUDA wheels on a CPU EC2 instance),
# then requirements.txt. Every later script is run with .venv/bin/python, so you never have to
# "activate" anything for setup. For interactive use:  source .venv/bin/activate
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M5_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$M5_DIR/.venv"

[[ $EUID -ne 0 ]] || { echo "Run as your normal user (no sudo) so .venv stays owned by you." >&2; exit 1; }

if [[ ! -x "$VENV/bin/python" ]]; then
  if ! python3 -m venv "$VENV" 2>/dev/null; then
    echo "python3-venv is missing; installing it (needs sudo) ..."
    rm -rf "$VENV"
    PYV="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    sudo apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "python${PYV}-venv" || sudo apt-get install -y python3-venv
    python3 -m venv "$VENV"
  fi
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet torch --index-url https://download.pytorch.org/whl/cpu
"$VENV/bin/python" -m pip install --quiet -r "$M5_DIR/requirements.txt"

"$VENV/bin/python" - <<'PY'
import importlib, sys
mods = ["torch", "sentence_transformers", "chromadb", "pypdf", "pymongo", "pymysql", "requests", "matplotlib", "numpy"]
for m in mods:
    mod = importlib.import_module(m)
    print(f"  {m:<22} {getattr(mod, '__version__', 'ok')}")
print(f"  python                 {sys.version.split()[0]}")
PY
echo "Python environment ready: $VENV"
