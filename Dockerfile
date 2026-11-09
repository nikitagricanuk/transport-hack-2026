# syntax=docker/dockerfile:1

# ---------- 1. build the React frontend ----------
FROM node:22.23.4-alpine3.24 AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 2. API runtime that also serves the built frontend ----------
FROM python:3.12.16-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    UDS_STATIC_DIR=/app/static \
    UDS_DATA_DIR=/data \
    HOME=/tmp
WORKDIR /app

# eclipse-sumo pulls in the SUMO binaries (hundreds of MB). The image that CI
# publishes, and `docker compose up` by default, leave it out. Opt in with
# `--build-arg WITH_SUMO=1` or `WITH_SUMO=1 docker compose up --build`.
ARG WITH_SUMO=0

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$WITH_SUMO" = "1" ]; then pip install --no-cache-dir eclipse-sumo==1.21.0; fi \
 && groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

COPY --chown=app:app backend/app ./app
COPY --chown=app:app --from=web /web/dist ./static
RUN mkdir -p /data && chown app:app /data

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
