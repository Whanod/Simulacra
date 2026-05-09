# syntax=docker/dockerfile:1.10
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY solana-plans ./solana-plans

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ".[api]"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEFI_SIM_REPO_ROOT=/app \
    DEFI_SIM_ARTIFACT_ROOT=/data/artifacts

RUN mkdir -p /data/artifacts

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --retries=12 --start-period=15s \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "defi_sim_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
