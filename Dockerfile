# One image, two roles, chosen by PETKIT_ROLE (ADR 0008):
#   api (default):  uvicorn, serves /api and the built frontend (Cloud Run service)
#   worker:         python -m backend.temporal.worker (Cloud Run worker pool)
#
# Build: gcloud builds submit --tag <REGION>-docker.pkg.dev/<PROJECT>/<REPO>/petkit:<BUILD_ID>

# --- Frontend build ---------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Python app -------------------------------------------------------------
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./backend/
COPY --from=frontend /app/frontend/dist ./frontend/dist

ENV PATH="/app/.venv/bin:$PATH" \
    FRONTEND_DIST_DIR=/app/frontend/dist \
    RUN_EMBEDDED_WORKER=false

RUN useradd --system --create-home petkit
USER petkit

# Cloud Run sets PORT for services; default to 8080 for local `docker run`.
# `exec` makes the app PID 1 so it receives SIGTERM directly.
EXPOSE 8080
CMD ["sh", "-c", "if [ \"$PETKIT_ROLE\" = worker ]; then exec python -m backend.temporal.worker; else exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}; fi"]
