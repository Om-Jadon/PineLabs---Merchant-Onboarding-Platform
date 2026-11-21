FROM node:24-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim-bookworm AS python-deps
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr-eng tesseract-ocr poppler-utils && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 merchant && install -d -m 700 -o merchant /var/lib/merchant-keys
FROM python-deps AS app
COPY backend/ ./
COPY fixtures /app/fixtures
COPY --from=web /web/dist /app/static
USER merchant
EXPOSE 8000
