# Data-Analysis-Agent (custom build) — container image for Alibaba Cloud ECS / 容器服务
FROM python:3.11-slim

# System libs: libgomp1 for duckdb/numpy, fonts for matplotlib chart rendering.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 fonts-noto-cjk curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_PORT=5001 \
    # Persist config + secrets to mounted volumes (see docker-compose.yml),
    # so they survive container rebuilds without shadowing source code.
    LLM_CONFIG_DIR=/app/persist/llm \
    DATASOURCE_CONFIG_DIR=/app/persist/data

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install -r requirements.txt gunicorn

COPY . .

# Writable runtime dirs (also mounted as volumes in compose).
RUN mkdir -p /app/persist/llm /app/persist/data /app/outputs /app/uploads

EXPOSE 5001

# IMPORTANT: a single worker only — session state (per-session DuckDB tables,
# the MCP manager's background event loop) lives in-process and is NOT shared
# across workers. Use threads for concurrency + SSE streaming. Long timeout so
# multi-step analyses streaming over SSE are not killed mid-run.
CMD ["gunicorn", "app:app", \
     "--workers", "1", \
     "--worker-class", "gthread", \
     "--threads", "16", \
     "--timeout", "600", \
     "--graceful-timeout", "30", \
     "--bind", "0.0.0.0:5001"]
