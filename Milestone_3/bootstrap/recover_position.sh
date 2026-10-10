#!/usr/bin/env bash
# Use only when grastate.dat has seqno -1 on every stopped node.
set -euo pipefail

data_dir=${1:?Usage: sudo $0 /mnt/gigavolt-mariadb/node1/data}
mysqld --wsrep-recover --datadir="$data_dir" 2>&1 | tee /tmp/gigavolt-wsrep-recover.log
echo 'Record the Recovered position from the output. Compare all nodes; bootstrap the highest position only after setting safe_to_bootstrap: 1 on that node.'
