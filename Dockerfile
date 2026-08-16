# Explicit rather than auto-detected: this repo carries a package.json for the
# Tailwind tooling, and the buildpack kept reading that as "Node project" and
# building an image with no Python in it. The compiled CSS is committed, so the
# production image needs Python and nothing else.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change doesn't reinstall the world.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static at build time so the running container serves a warm,
# hash-manifested set. A dummy key is enough — settings only needs *a* value
# here, and the real one arrives from the environment at runtime.
RUN DJANGO_SECRET_KEY=build-only-not-a-real-secret \
    DJANGO_DEBUG=False \
    python manage.py collectstatic --noinput

EXPOSE 8000

# Railway injects $PORT. Shell form so it expands.
CMD gunicorn varsity.wsgi \
      --bind 0.0.0.0:${PORT:-8000} \
      --workers 3 \
      --timeout 60 \
      --access-logfile -
