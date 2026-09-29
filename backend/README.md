# ctxads — backend

Python-часть проекта: бот MAX, подбор рекламы, API мини-приложения, трекер кликов, миграции.
Общее описание и быстрый старт — в [README в корне репозитория](../README.md).

Все команды ниже выполняются из папки `backend/`:

```bash
uv sync                                   # зависимости
uv run alembic upgrade head               # миграции
uv run python -m ctxads.seed              # демо-каталог объявлений
uv run python -m ctxads.run --mode polling
uv run pytest -q                          # тесты (нужен PostgreSQL: ctx_test)
uv run ruff check . && uv run ruff format --check .
```

Конфиг читается из `.env` в корне репозитория (`../.env`); `backend/.env`, если он есть,
приоритетнее. Шаблон — `../.env.example`.

| Папка | Что внутри |
|---|---|
| `src/ctxads/` | код приложения |
| `alembic/` | миграции БД |
| `seed/ads.yaml` | демо-каталог объявлений |
| `tests/` | юнит- и интеграционные тесты (MAX и LLM замоканы) |
| `docs/` | архитектура, запуск, демо, сценарии для фронтенда, мини-приложение |
| `Caddyfile` | HTTPS-прокси для webhook-режима (используется в `compose.yaml`) |
