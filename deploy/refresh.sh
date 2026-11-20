#!/bin/sh
# Runs ON THE SERVER, from cron (deploy/urbangreen.cron): adds the new Sentinel-2 and Landsat
# images of every commune and retries the dates in error.
#   /srv/urbangreen/deploy/refresh.sh      (log: /var/log/urbangreen-refresh.log)
cd "$(dirname "$0")/.."
echo "==> $(date '+%Y-%m-%d %H:%M') refresh"
docker compose exec -T web python manage.py refresh_all
code=$?
echo "==> $(date '+%Y-%m-%d %H:%M') done (exit $code)"
exit $code
