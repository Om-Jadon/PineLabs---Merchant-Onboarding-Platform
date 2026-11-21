FROM postgres:17-alpine AS database
RUN apk --no-cache upgrade
COPY backend/postgres/pg_hba.conf /etc/postgresql/merchant_hba.conf

FROM node:24-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim-bookworm AS python-deps
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr-eng tesseract-ocr poppler-utils util-linux && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip==26.3 && python -m pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 merchant && install -d -m 700 -o merchant \
    /var/lib/merchant-keys /var/lib/merchant-requests /var/lib/merchant-webhooks \
    /var/lib/workflow-login /var/lib/provider-login
FROM python-deps AS app
COPY backend/ ./
COPY fixtures /app/fixtures
COPY --from=web /web/dist /app/static
USER merchant
EXPOSE 8000
