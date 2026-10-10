#!/usr/bin/env bash
# Starts exactly one explicitly selected, verified primary component.
set -euo pipefail

usage() {
  echo "Usage: sudo $0 --data-dir /mnt/gigavolt-mariadb/node1/data" >&2
  exit 64
}

[[ $# -eq 2 && $1 == '--data-dir' ]] || usage
data_dir=$2
state_file="$data_dir/grastate.dat"
[[ -r "$state_file" ]] || { echo "Cannot read $state_file" >&2; exit 1; }

safe=$(awk -F: '/^[[:space:]]*safe_to_bootstrap:/ {gsub(/[[:space:]]/, "", $2); print $2}' "$state_file")
[[ $safe == 1 ]] || {
  echo "Refusing bootstrap: $state_file does not show safe_to_bootstrap: 1" >&2
  echo 'Run inspect_grastate.sh and, if needed, wsrep recovery first.' >&2
  exit 1
}

echo 'Bootstrapping the verified primary component...'
galera_new_cluster
echo 'Primary component started. Verify wsrep_cluster_status=Primary and wsrep_cluster_size=1 before joining nodes.'
