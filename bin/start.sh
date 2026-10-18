#!/usr/bin/env sh
# Production start command (used by Render): apply database migrations, then
# start gunicorn. Migrations run here rather than in a pre-deploy step so they
# also run on plans without pre-deploy commands.
set -e

flask --app wsgi db upgrade

exec gunicorn wsgi:app \
  --bind "0.0.0.0:${PORT:-10000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout 60 \
  --access-logfile -
