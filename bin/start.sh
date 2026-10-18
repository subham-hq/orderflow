#!/usr/bin/env sh
# Production start command (used by Render): apply database migrations, then
# start gunicorn. Migrations run here rather than in a pre-deploy step so they
# also run on plans without pre-deploy commands.
set -e

flask --app wsgi db upgrade

# The access log prints the path the app stores in orderflow.log_path
# (orderflow/security.py): no query string or Referer, and the token in links
# from emails (/reset-password/, /invite/, /verify/) replaced by [token], so
# anyone reading the logs can't use a link meant for someone else.
exec gunicorn wsgi:app \
  --bind "0.0.0.0:${PORT:-10000}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --threads "${GUNICORN_THREADS:-8}" \
  --timeout 60 \
  --access-logfile - \
  --access-logformat '%(h)s "%(m)s %({orderflow.log_path}e)s" %(s)s %(b)s %(M)sms "%(a)s"'
