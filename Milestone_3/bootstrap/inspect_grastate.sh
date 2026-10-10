#!/usr/bin/env bash
# Read-only inspection: run after all three MariaDB nodes are confirmed stopped.
set -euo pipefail

if (( $# == 0 )); then
  echo "Usage: $0 /path/to/node1/data [/path/to/node2/data ...]" >&2
  exit 64
fi

printf '%-36s %-8s %-38s\n' 'DATA DIRECTORY' 'SAFE' 'SEQNO'
for data_dir in "$@"; do
  state_file="$data_dir/grastate.dat"
  if [[ ! -r "$state_file" ]]; then
    printf '%-36s %-8s %-38s\n' "$data_dir" 'MISSING' 'MISSING'
    continue
  fi
  safe=$(awk -F: '/^[[:space:]]*safe_to_bootstrap:/ {gsub(/[[:space:]]/, "", $2); print $2}' "$state_file")
  seqno=$(awk -F: '/^[[:space:]]*seqno:/ {gsub(/[[:space:]]/, "", $2); print $2}' "$state_file")
  printf '%-36s %-8s %-38s\n' "$data_dir" "${safe:-UNKNOWN}" "${seqno:-UNKNOWN}"
done

echo
echo 'Bootstrap only a node whose grastate.dat reports safe_to_bootstrap: 1.'
echo 'If every seqno is -1, recover the position with mysqld --wsrep-recover on each node and choose the highest recovered sequence number.'
