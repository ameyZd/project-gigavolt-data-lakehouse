#!/usr/bin/env bash
# Project Gigavolt | Milestone 5 | Install MongoDB Community on Ubuntu (EC2)
#
# Usage:
#   sudo bash setup/01_install_mongodb.sh            # native apt install (default, recommended)
#   sudo bash setup/01_install_mongodb.sh docker     # run MongoDB in Docker instead
#
# Optional environment variable: MONGO_SERIES=8.0 (MongoDB release series for the apt repo)
set -Eeuo pipefail

MODE="${1:-apt}"
MONGO_SERIES="${MONGO_SERIES:-8.0}"
DATA_DIR=/opt/data/mongodb
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M5_DIR="$(dirname "$SCRIPT_DIR")"

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

wait_for_mongo() {
  local cmd=("$@")
  for _ in $(seq 1 30); do
    if "${cmd[@]}" --quiet --eval "db.adminCommand('ping').ok" 2>/dev/null | grep -q 1; then
      echo "MongoDB is accepting connections."
      return 0
    fi
    sleep 2
  done
  echo "MongoDB did not become ready in 60 s. Check: journalctl -u mongod -n 50" >&2
  return 1
}

if [[ "$MODE" == "docker" ]]; then
  if ! command -v docker >/dev/null; then
    apt-get update -y
    apt-get install -y docker.io
  fi
  systemctl enable --now docker
  install -d -m 0755 "$DATA_DIR"
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "$M5_DIR/config/docker-compose.mongodb.yml" up -d
  else
    # Same settings as config/docker-compose.mongodb.yml, for hosts without the compose plugin.
    docker rm -f gigavolt-mongo >/dev/null 2>&1 || true
    docker run -d --name gigavolt-mongo --restart unless-stopped \
      -p 127.0.0.1:27017:27017 -v "$DATA_DIR":/data/db \
      mongo:8.0 --bind_ip_all --wiredTigerCacheSizeGB 0.25 --slowms 50
  fi
  wait_for_mongo docker exec gigavolt-mongo mongosh
  docker exec gigavolt-mongo mongosh --quiet --eval 'db.version()'
  exit 0
fi

# ---------- native apt install ----------
. /etc/os-release
CODENAME="${VERSION_CODENAME:-noble}"
case "$CODENAME" in
  focal|jammy|noble) ;;
  *) echo "Ubuntu '$CODENAME' is not in MongoDB's supported list; trying the 'noble' repo." >&2; CODENAME=noble ;;
esac
echo "Installing MongoDB $MONGO_SERIES for Ubuntu $CODENAME ..."

apt-get update -y
apt-get install -y gnupg curl
curl -fsSL "https://www.mongodb.org/static/pgp/server-${MONGO_SERIES}.asc" \
  | gpg --batch --yes -o "/usr/share/keyrings/mongodb-server-${MONGO_SERIES}.gpg" --dearmor
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-${MONGO_SERIES}.gpg ] https://repo.mongodb.org/apt/ubuntu ${CODENAME}/mongodb-org/${MONGO_SERIES} multiverse" \
  > "/etc/apt/sources.list.d/mongodb-org-${MONGO_SERIES}.list"
apt-get update -y
apt-get install -y mongodb-org

# Service account differs by package build ("mongodb" on Ubuntu, "mongod" on RHEL-style builds).
MONGO_USER=mongodb
id -u "$MONGO_USER" >/dev/null 2>&1 || MONGO_USER=mongod

install -d -m 0750 -o "$MONGO_USER" -g "$MONGO_USER" "$DATA_DIR"
install -d -m 0755 -o "$MONGO_USER" -g "$MONGO_USER" /var/log/mongodb

if [[ -f /etc/mongod.conf && ! -f /etc/mongod.conf.orig ]]; then
  cp /etc/mongod.conf /etc/mongod.conf.orig
fi
install -m 0644 "$M5_DIR/config/mongod.conf" /etc/mongod.conf

systemctl daemon-reload
systemctl enable mongod
systemctl restart mongod
wait_for_mongo mongosh

mongosh --quiet --eval 'print("MongoDB version: " + db.version()); printjson(db.adminCommand({getCmdLineOpts:1}).parsed)'
systemctl --no-pager --lines=0 status mongod
echo "Done. Config: /etc/mongod.conf  Data: $DATA_DIR  Log: /var/log/mongodb/mongod.log"
