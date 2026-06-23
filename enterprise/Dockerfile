# karst enterprise gateway — one authenticated, metered MCP endpoint for a team.
# The free `karst` core is installed from PyPI; this image adds the gateway
# (Postgres + SSO + usage/audit) on top. Build context = this `enterprise/` dir.
#
#   docker build -t karst-gateway -f enterprise/Dockerfile enterprise/
#   (or just `docker compose up` — see docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install runtime deps first for layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# The enterprise package isn't on PyPI — copy it in so `enterprise.gateway` resolves.
COPY . /app/enterprise

# Run as a non-root user.
RUN useradd --create-home --uid 10001 karst && chown -R karst /app
USER karst

EXPOSE 8080

# Liveness: the gateway leaves GET /healthz open (no auth) for probes.
HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz', timeout=3).status==200 else 1)" || exit 1

# DATABASE_URL (postgres://…) and any KARST_OIDC_* are injected at runtime.
CMD ["python", "-m", "enterprise.gateway.cli", "serve", "--host", "0.0.0.0", "--port", "8080"]
