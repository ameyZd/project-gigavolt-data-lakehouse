#!/usr/bin/env bash
# Project Gigavolt | Milestone 5 | One-command setup on a fresh Ubuntu EC2 instance
#
#   cd ~/project-gigavolt-data-lakehouse/Milestone_5
#   bash setup/run_all.sh              # everything, in order (asks once: free Groq key, or Enter = local Ollama)
#   bash setup/run_all.sh --from 4     # resume from step 4 after fixing a problem
#
# Run as the normal "ubuntu" user, NOT with sudo: the script calls sudo itself only for the
# system steps (1, 2, 5) so .env, .venv and data/ stay owned by you.
#
#   0  .env (generated MariaDB password + free LLM choice)       setup/00_configure_env.sh
#   1  MariaDB (single instance) + Milestone 1-2 ERP data        setup/01_install_mariadb_and_load_erp.sh  [sudo]
#   2  MongoDB 8.0 (document store)                              setup/02_install_mongodb.sh               [sudo]
#   3  Python venv (CPU torch + requirements)                    setup/03_python_env.sh
#      + local Ollama model, only if you chose no Groq key      setup/03b_install_ollama.sh               [sudo]
#   4  Telemetry -> MongoDB, PDFs -> ChromaDB                    setup/04_load_data.sh
#   5  RAG views + SELECT-only MariaDB user                      setup/05_create_mariadb_rag_access.sh     [sudo]
#   6  Offline tests + live health check                         setup/06_verify.sh
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M5_DIR="$(dirname "$SCRIPT_DIR")"
FROM=0
[[ ${1:-} == --from ]] && FROM="${2:?usage: bash setup/run_all.sh --from N}"
[[ $EUID -ne 0 ]] || { echo "Run as the normal user: bash setup/run_all.sh (no sudo)." >&2; exit 1; }
cd "$M5_DIR"

banner() { printf '\n\033[1;36m=== Step %s/6: %s ===\033[0m\n' "$1" "$2"; }
trap 'echo; echo -e "\033[1;31mSetup stopped at step ${CURRENT:-?}. Fix the error above, then: bash setup/run_all.sh --from ${CURRENT:-0}\033[0m"' ERR

[[ "$(uname -s)" == Linux ]] || { echo "This setup is for the Ubuntu EC2 instance, not $(uname -s). SSH into EC2 first." >&2; exit 1; }
if ! sudo -n true 2>/dev/null; then
  cat >&2 <<MSG
User '$(whoami)' on $(hostname) cannot use sudo without a password.
On EC2 the default 'ubuntu' user has password-free sudo. Log in as ubuntu with your key pair:
    ssh -i <your-key>.pem ubuntu@<instance-public-IP>
(or EC2 console -> Connect -> EC2 Instance Connect, user name: ubuntu), then run: bash setup/run_all.sh
MSG
  exit 1
fi

# Small instances: MariaDB + MongoDB + PyTorch need headroom. Add 2 GB swap if RAM < 3 GB and no swap exists.
MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if (( MEM_KB < 3000000 )) && [[ -z "$(swapon --show --noheadings)" ]]; then
  echo "RAM is $((MEM_KB / 1024)) MB with no swap; adding a 2 GB swap file at /swapfile ..."
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

if (( FROM <= 0 )); then CURRENT=0; banner 0 "Configure .env";                     bash  setup/00_configure_env.sh; fi
if (( FROM <= 1 )); then CURRENT=1; banner 1 "MariaDB + ERP data (single instance)"; sudo bash setup/01_install_mariadb_and_load_erp.sh; fi
if (( FROM <= 2 )); then CURRENT=2; banner 2 "MongoDB document store"
  sudo bash setup/02_install_mongodb.sh || { echo "apt install failed; falling back to Docker ..."; sudo bash setup/02_install_mongodb.sh docker; }
fi
if (( FROM <= 3 )); then CURRENT=3; banner 3 "Python environment (+ local LLM if chosen)"
  bash setup/03_python_env.sh
  if grep -qE '^LLM_PROVIDER=ollama' .env; then sudo bash setup/03b_install_ollama.sh; fi
fi
if (( FROM <= 4 )); then CURRENT=4; banner 4 "Load telemetry + build vector store"; bash setup/04_load_data.sh; fi
if (( FROM <= 5 )); then CURRENT=5; banner 5 "RAG views + read-only SQL user";   sudo bash setup/05_create_mariadb_rag_access.sh; fi
if (( FROM <= 6 )); then CURRENT=6; banner 6 "Verify";                           bash  setup/06_verify.sh; fi

trap - ERR
cat <<'DONE'

==================================================================
 Milestone 5 setup complete. Try it:
   source .venv/bin/activate
   python scripts/02_telemetry_queries.py      # indexed diagnostic queries (IXSCAN)
   python scripts/ask.py -i                    # ask questions live
   python scripts/04_run_test_queries.py --quiet
   python scripts/05_benchmark_latency.py --repeats 3
==================================================================
DONE
