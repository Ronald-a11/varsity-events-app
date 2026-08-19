release: python manage.py migrate --noinput
web: gunicorn varsity.wsgi --bind 0.0.0.0:$PORT --workers 3 --timeout 60 --access-logfile -
# Sends mail and releases seats held by abandoned checkouts. Without it the
# site still works — tasks fall back to running inline — but a page render is
# only as fast as the mail server, and freed seats aren't offered to the
# waitlist until something sweeps for them.
worker: python manage.py qcluster
