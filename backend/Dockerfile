# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS skill-dependencies

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
# Stable skill/runtime dependency layer; changes to application code do not invalidate it.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS application
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    U2NET_HOME=/models/rembg TRYON_OUTPUT_DIR=/app/data/tryon_output
COPY --from=skill-dependencies /app/.venv /app/.venv
# Vendored skill and its assets form a separate cacheable capability layer.
COPY vendor/shop-tryon-skill /app/vendor/shop-tryon-skill
COPY backend /app/backend
COPY scripts /app/scripts
COPY sync_env.py pyproject.toml /app/
RUN mkdir -p /app/data/sessions /models/rembg
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
