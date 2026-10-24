# MILAAN production image. Not for public use on its own: run it behind a
# TLS-terminating reverse proxy and configure authentication (see README,
# "Production deployment"). The app refuses to start without credentials.
FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt
COPY . ./
RUN useradd --create-home --uid 10001 milaan && mkdir -p /data && chown -R milaan:milaan /app /data
USER milaan
ENV APP_ENV=production \
    MILAAN_AUTH_REQUIRED=true \
    DATABASE_PATH=/data/milaan.db \
    AGENT_DB_PATH=/data/milaan_agent.db
# Required at runtime (never baked into the image):
#   MILAAN_BASIC_AUTH_USER, MILAAN_BASIC_AUTH_PASSWORD_HASH,
#   MILAAN_ALLOWED_HOSTS=<public host name used by the proxy>
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python scripts/healthcheck.py
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "wsgi:app"]
