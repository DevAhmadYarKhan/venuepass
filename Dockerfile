# syntax=docker/dockerfile:1
# Both stages use the same Python base so the copied environment stays compatible.
FROM python:3.14-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

FROM base AS builder
# uv is build tooling only; the final image runs the already-installed executables.
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
# Keep dependency installation cached independently of application source changes.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM base AS runtime
ENV PATH="/app/.venv/bin:$PATH"
# The application needs no writable source directory or elevated privileges.
RUN groupadd --gid 10001 venuepass \
    && useradd --uid 10001 --gid venuepass --no-create-home \
        --home-dir /app --shell /usr/sbin/nologin venuepass
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
USER venuepass
EXPOSE 8000
# This is a liveness check; database readiness is checked separately by Compose.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=3).close()"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
