#!/bin/sh
# Runs ON THE SERVER: pull the latest code from GitHub and rebuild the container.
#   /srv/urbangreen/deploy/update.sh
# The data volume ("urbangreen_data": database + rasters) and .env are kept.
set -e
cd "$(dirname "$0")/.."
git fetch --quiet origin main
git reset --hard --quiet origin/main     # the server copy always mirrors GitHub
echo "==> Code at $(git log -1 --format='%h %s')"
docker compose up -d --build --remove-orphans
install -m 644 deploy/urbangreen.cron /etc/cron.d/urbangreen   # quarterly refresh_all
docker image prune -f >/dev/null
sleep 5
docker compose ps
