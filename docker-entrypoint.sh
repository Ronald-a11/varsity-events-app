#!/bin/sh
# One image, two jobs.
#
# The web service and the task cluster run identical code and identical
# dependencies; the only difference is which process starts. Railway can set a
# custom start command per service, but only through the dashboard — a click
# nobody remembers making and nothing records. Keying it off an environment
# variable instead means the worker is provisioned the same way as everything
# else here, and `railway variables` shows what a service actually is.
#
#   PROCESS_TYPE unset or "web"  -> gunicorn          (the default; existing
#                                                      services keep working
#                                                      with no change at all)
#   PROCESS_TYPE=worker          -> manage.py qcluster
#
set -e

case "${PROCESS_TYPE:-web}" in
  worker)
    echo "Starting the task cluster (PROCESS_TYPE=worker)"
    # Sends mail off the request thread and sweeps abandoned checkouts. Django-q
    # reads its own config from Q_CLUSTER, so there is nothing to pass here.
    exec python manage.py qcluster
    ;;
  web)
    echo "Starting the web server (PROCESS_TYPE=${PROCESS_TYPE:-web})"
    # Railway injects $PORT.
    exec gunicorn varsity.wsgi \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers "${GUNICORN_WORKERS:-3}" \
      --timeout 60 \
      --access-logfile -
    ;;
  *)
    # Fail loudly rather than silently starting the wrong thing: a typo here
    # would otherwise present as a web service that never serves, or a worker
    # that quietly isn't one.
    echo "Unknown PROCESS_TYPE '${PROCESS_TYPE}' — expected 'web' or 'worker'." >&2
    exit 1
    ;;
esac
