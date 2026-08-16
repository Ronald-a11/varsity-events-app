release: python manage.py migrate --noinput
web: gunicorn varsity.wsgi --bind 0.0.0.0:$PORT --workers 3 --timeout 60 --access-logfile -
