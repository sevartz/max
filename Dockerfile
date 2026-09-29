# Сборка мини-приложения (frontend/) → статика, которую отдаёт тот же FastAPI по /miniapp/.
FROM node:22-slim AS frontend-build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./frontend
RUN npm run build

# Backend: бот MAX + API мини-приложения + трекер кликов.
FROM python:3.12-slim AS app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/huggingface
WORKDIR /app

# Сначала зависимости (слой кэшируется), потом код.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/src ./src
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/seed ./seed
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
RUN uv sync --frozen --no-dev

EXPOSE 8000
# MODE=polling|webhook. Миграции идемпотентны — применяем при каждом старте.
ENV MODE=webhook
CMD ["sh", "-c", "uv run --no-sync alembic upgrade head && uv run --no-sync python -m ctxads.run --mode $MODE"]
