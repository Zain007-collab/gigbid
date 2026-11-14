# Single-container image for free hosts (e.g. Hugging Face Spaces / Render): API + built web app + embedded
# GigSim + inline worker. docker compose runs the same code as separate services instead.
FROM node:22-alpine AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
RUN npx tsc -b && npx vite build

FROM python:3.12-slim
# Small-instance settings (Render free: 512 MB, 0.1 CPU): single-threaded ONNX, one inline run at a time,
# fewer malloc arenas, model + portfolio embeddings baked in at build time, read-only embedding cache at runtime.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    GIGSIM_EMBEDDED=1 GIGBID_MCP_TRANSPORT=memory WORKER_MODE=inline PORT=7860 \
    INLINE_CONCURRENCY=1 FASTEMBED_THREADS=1 OMP_NUM_THREADS=1 MALLOC_ARENA_MAX=2 \
    FASTEMBED_CACHE_PATH=/app/models/fastembed RAG_CACHE_DIR=/app/models/emb_cache WARM_REVIEWER_JOBS=8
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --only-binary :all: -r requirements.lock.txt
COPY . .
COPY --from=web /web/dist apps/web/dist
RUN python -m scripts.warm_cache
ENV RAG_CACHE_READONLY=1
RUN useradd -m app && mkdir -p data/processed && chown -R app /app
USER app
EXPOSE 7860
CMD ["sh", "-c", "GIGSIM_BASE_URL=${GIGSIM_BASE_URL:-http://127.0.0.1:${PORT}/gigsim} uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT}"]
