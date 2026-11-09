# ---------- 1. build the React frontend ----------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY frontend/ ./
RUN npm run build

# ---------- 2. Python API that also serves the built frontend ----------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UDS_STATIC_DIR=/app/static \
    UDS_DATA_DIR=/data
WORKDIR /app

# WITH_SUMO=1 adds SUMO netconvert for the local "SUMO-проверка" button (~150 MB)
ARG WITH_SUMO=1
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$WITH_SUMO" = "1" ]; then pip install --no-cache-dir eclipse-sumo==1.21.0; fi

COPY backend/app ./app
COPY --from=web /web/dist ./static
RUN mkdir -p /data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
