# Single-container image for free hosts (e.g. Hugging Face Spaces / Render): API + built web app + embedded
# GigSim + inline worker. docker compose runs the same code as separate services instead.
FROM node:22-alpine AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
RUN npx tsc -b && npx vite build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    GIGSIM_EMBEDDED=1 GIGBID_MCP_TRANSPORT=memory WORKER_MODE=inline PORT=7860
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --only-binary :all: -r requirements.lock.txt
COPY . .
COPY --from=web /web/dist apps/web/dist
RUN useradd -m app && mkdir -p data/processed && chown -R app /app
USER app
EXPOSE 7860
CMD ["sh", "-c", "GIGSIM_BASE_URL=${GIGSIM_BASE_URL:-http://127.0.0.1:${PORT}/gigsim} uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT}"]
