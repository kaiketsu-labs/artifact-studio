# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.14-slim-bookworm

LABEL org.opencontainers.image.title="Artifact Studio" \
      org.opencontainers.image.source="https://github.com/kaiketsu-labs/artifact-studio"

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./

# Optional combined CA bundle for builders behind an HTTPS inspection proxy.
# The secret and temporary apt configuration never become part of the image.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      export SSL_CERT_FILE=/run/secrets/proxy_ca \
             NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca \
             APT_CONFIG=/tmp/build-apt.conf; \
      printf 'Acquire::https::CaInfo "/run/secrets/proxy_ca";\n' > "$APT_CONFIG"; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends git gh tesseract-ocr tesseract-ocr-eng \
    && uv sync --locked --no-dev --no-cache \
    && playwright install --with-deps chromium --only-shell \
    && chmod -R a+rX /opt/playwright \
    && rm -rf /var/lib/apt/lists/* /tmp/build-apt.conf

RUN groupadd --gid 1000 studio \
    && useradd --uid 1000 --gid studio --create-home studio \
    && mkdir -p /data/collection /data/state /data/config/gh /data/imports \
    && chown -R studio:studio /data \
    && chmod 700 /data/config/gh \
    && git config --system credential.https://github.com.helper '!/usr/bin/gh auth git-credential'

COPY artifact_studio/ artifact_studio/
COPY web/ web/
COPY runtime/ runtime/
COPY agent/ agent/
COPY docs/ docs/
# Some hosts check out files with restrictive modes. Keep source owned by root
# but readable by the runtime user, independent of the host's umask.
RUN chmod -R a+rX artifact_studio web runtime agent docs \
    && chmod a+r pyproject.toml uv.lock

ENV ARTIFACT_STUDIO_COLLECTION=/data/collection \
    ARTIFACT_STUDIO_DATABASE=/data/state/review.sqlite3 \
    ARTIFACT_STUDIO_UPDATE_CHECKS=0 \
    GH_CONFIG_DIR=/data/config/gh
USER studio
VOLUME ["/data"]
EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import http.client; c = http.client.HTTPConnection('127.0.0.1', 8790, timeout=3); c.request('GET', '/api/health'); r = c.getresponse(); raise SystemExit(0 if r.status == 200 else 1)"
CMD ["uvicorn", "artifact_studio.app:app", "--host", "0.0.0.0", "--port", "8790", "--no-proxy-headers"]
