# ─────────────────────────────────────────────────────────────────────────────
# AI Video API image. Runs the FastAPI service and/or the RQ worker.
# The ComfyUI + CUDA runtime is provided by the RunPod base image / a sibling
# container; this image only needs CPU Python to orchestrate it.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps: libjpeg/zlib for Pillow, curl for healthcheck, supervisor.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo zlib1g libwebp7 curl supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/api

COPY requirements.txt pyproject.toml ./
# asyncpg → PostgreSQL driver (used by the Selectel orchestrator deployment).
RUN pip install -r requirements.txt asyncpg

COPY app ./app
COPY config ./config
COPY scripts ./scripts
COPY deploy ./deploy

RUN chmod +x deploy/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Default: run API. Override CMD with "worker" or "all" (see entrypoint).
ENTRYPOINT ["deploy/entrypoint.sh"]
CMD ["api"]
