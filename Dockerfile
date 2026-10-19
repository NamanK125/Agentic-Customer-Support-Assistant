# syntax=docker/dockerfile:1.7
# PulseNest support assistant API. Build from the repo root:  docker build -t support-assistant .
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY data ./data
COPY kb ./kb
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --home /app app \
    && mkdir -p /app/.cache/huggingface && chown -R app /app/.cache
USER app

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

# One worker per container: scale by replicas, not by processes inside a container.
CMD ["uvicorn", "support_assistant.api:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
