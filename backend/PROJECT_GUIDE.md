# Руководство по проекту ctxads

> Рабочее название `ctxads` — переименуй при желании (пакет, compose, .env).
>
> Репозиторий: этот файл и весь Python — в `backend/` (команды `uv …` — отсюда), мини-приложение — в `../frontend/`,
> `compose.yaml`, `Dockerfile`, `package.json` и `.env` — в корне (`docker compose` и `npm` — из корня).

## Что строим (MVP для хакатона MAX, трек «Эффективный бизнес»)

Бот-администратор канала в мессенджере MAX. Когда в канале выходит пост, бот:
1. анализирует текст поста (тема, категория, brand safety);
2. подбирает из каталога **рекламу, контекстно подходящую именно к этому посту**;
3. присылает админу канала в личку предложение с кнопками **Одобрить / Отклонить / Другой вариант / Больше не предлагать категорию**;
4. после одобрения публикует рекламный пост в канал (с маркировкой) и считает клики/просмотры.

Главная ценность: реклама подбирается под конкретный пост, а админ контролирует каждое размещение.
Мы НЕ делаем в MVP: реальные платежи, настоящую маркировку через ОРД (ERID мокаем), SDK для чужих ботов, кабинет рекламодателя с оплатой.

Подробная архитектура — `docs/architecture.md`. Прочитай её перед началом любой крупной задачи.

## Стек

- Python 3.12, менеджер пакетов **uv**
- FastAPI + uvicorn — webhook, редирект-трекер кликов, простые страницы
- httpx (async) — собственный тонкий клиент MAX Bot API (`src/ctxads/max_api/`)
- Pydantic v2 + pydantic-settings — модели и конфиг из `.env`
- SQLAlchemy 2.0 (async) + asyncpg + Alembic; PostgreSQL 16 + **pgvector**
- Фоновые задачи: таблица `jobs` в Postgres + in-process воркер (`SELECT … FOR UPDATE SKIP LOCKED`). Redis не используем.
- LLM: **NVIDIA NIM** (build.nvidia.com) через OpenAI-совместимый API — провайдер по умолчанию. Интерфейс `LLMClient` с реализациями `openai_compat` (NIM), YandexGPT / GigaChat (прод-альтернатива) и `FakeLLM` (для тестов). Подробности — раздел «LLM: NVIDIA NIM» ниже.
- Эмбеддинги: интерфейс `Embedder`, по умолчанию локальная `intfloat/multilingual-e5-small` (dim 384). Через NIM эмбеддинги НЕ считаем — бережём кредиты.
- Тесты: pytest, pytest-asyncio, respx (мок HTTP к MAX), без сети
- Линт/формат: ruff

## Команды

```bash
uv sync                                   # зависимости
docker compose up -d db                   # Postgres + pgvector
uv run alembic upgrade head               # миграции
uv run python -m ctxads.seed              # засеять каталог рекламы из seed/ads.yaml
uv run python -m ctxads.run --mode polling   # локальная разработка (long polling)
uv run python -m ctxads.run --mode webhook   # прод/демо (нужен HTTPS на 443)
uv run pytest -q
uv run ruff check . && uv run ruff format .
```

## Факты о MAX Bot API (проверены по dev.max.ru, сентябрь 2026)

- Base URL: `https://platform-api2.max.ru` (НЕ `platform-api.max.ru`). Держи в конфиге `MAX_API_BASE`.
- Авторизация: заголовок `Authorization: <token>`. Токен в query больше не поддерживается.
- Лимиты: ≤ 30 rps на бота; ≤ 2 сообщения/сек в один чат/канал → клиентский rate limiter обязателен.
- Отправка: `POST /messages?chat_id=<id>` (канал) или `?user_id=<id>` (личка). Тело: `text` (≤ 4000), `attachments`, `format` (`markdown`|`html`), `notify`. Для каналов `notify` не передаём или `true`.
- Inline-клавиатура: attachment `{"type":"inline_keyboard","payload":{"buttons":[[...]]}}`. Кнопки `callback` (нажатие → событие `message_callback`) и `link` (≤ 3 в ряду, url ≤ 2048).
- Ответ на нажатие: `POST /answers` — схему тела ПРОВЕРЬ на https://dev.max.ru/docs-api/methods/POST/answers перед реализацией.
- События (объект Update, поле `update_type`): `bot_added` (есть `chat_id`, `user` — кто добавил, `is_channel`), `bot_removed`, `bot_started`, `bot_stopped`, `message_created`, `message_edited`, `message_removed`, `message_callback`, `bot_admin_permissions_changed`.
- Webhook: `POST /subscriptions` с `url`, `update_types`, `secret`. Только HTTPS на порту 443, сертификат доверенного CA (Let's Encrypt ок, self-signed нет). Проверяй заголовок `X-Max-Bot-Api-Secret`. Отвечать 200 **в течение 30 сек**, иначе ретраи (до 10, экспоненциально) → возможны дубли. 8 часов без 200 → автоотписка. При активной подписке long polling не работает.
- Long polling: `GET /updates` — только для локальной разработки.
- Права бота-админа канала: для чтения постов и постинга нужны `read_all_messages` + `write`. Без `read_all_messages` бот не получает события. `edit`/`delete` — для редактирования/удаления постов в каналах. `view_stats` ботам недоступно.
- Проверить свои права: `GET /chats/{chatId}/members/me` или `GET /chats/{chatId}/members/admins`.
- Посты канала: `GET /messages?chat_id=…`; объект Message для постов канала содержит статистику — точные поля ПРОВЕРЬ на https://dev.max.ru/docs-api/objects/Message.

**Правило:** не выдумывай эндпоинты и поля. Если чего-то нет в этом файле или в `docs/architecture.md` — открой соответствующую страницу dev.max.ru и проверь. Если проверить нельзя — оставь `# TODO(verify-api): …` и сделай код устойчивым к отсутствию поля.

## LLM: NVIDIA NIM

- Endpoint: `https://integrate.api.nvidia.com/v1`, OpenAI-совместимый (`/chat/completions`). Клиент — пакет `openai` с `base_url` из конфига, ключ `nvapi-...` в `LLM_API_KEY`.
- Модель — из `LLM_MODEL`; точное имя бери из каталога build.nvidia.com (предпочтительно Qwen или DeepSeek — хорошо держат русский и JSON). Режим рассуждений (thinking/reasoning), если у модели он есть, **выключен**.
- Бесплатный тариф: ~1000 кредитов (≈ 1 вызов = 1 кредит), ~40 запросов/мин. У нас 2 вызова на пост (анализ + объяснение) → кредиты расходуются только живыми постами.
- Отсюда правила:
  - тесты и CI **никогда** не ходят в NIM — только `FakeLLM`;
  - клиентский лимит `LLM_RPM` (по умолчанию 30) и ретраи с экспоненциальным backoff на 429/5xx (макс. 3 попытки);
  - кэш анализа по хэшу текста поста (одинаковый пост не анализируем дважды);
  - если LLM недоступна после ретраев — пост пропускаем с причиной `llm_unavailable`, бот не падает;
  - опциональный `LLM_FALLBACK_PROVIDER` (например `yandexgpt`) — используется при исчерпании кредитов/постоянных 429.
- Ответы требуем строго в JSON: `response_format={"type":"json_object"}`, если модель поддерживает; иначе — извлечение первого JSON-объекта из текста + валидация pydantic, при невалидном ответе одна повторная попытка.
- `temperature` 0.2 для анализа, 0.5 для объяснения; `max_tokens` ограничен (400 / 150).
- В LLM уходит только публичный текст поста и описание объявления. Никаких user_id, имён и данных админов.
- Для питча: на хакатоне — NIM, в проде — российский провайдер (YandexGPT/GigaChat) переключением `LLM_PROVIDER`, код не меняется.

## Архитектурные правила

1. **Webhook-хендлер ничего не считает.** Он проверяет secret, дедуплицирует, кладёт Update в `jobs` и сразу отвечает 200. LLM и сеть — только в воркере.
2. **Идемпотентность.** Таблица `processed_updates` с уникальным ключом (update_type, chat_id, message mid / callback_id, timestamp). Повторы игнорируем.
3. **Игнорируй собственные сообщения бота** (sender.user_id == id бота из `GET /me`, кэшируется при старте) и наши рекламные посты.
4. Один транспорт-агностичный `Dispatcher`: и polling, и webhook отдают ему `Update`.
5. Слои: `max_api` (HTTP) → `bot/handlers` (сценарии) → `matching`, `ads`, `billing` (домен) → `db/repo` (данные). Хендлеры не пишут SQL напрямую.
6. Все пользовательские тексты — на русском, в `src/ctxads/bot/texts.py`. Никаких строк в хендлерах.
7. Callback payload — короткий: `"p:<proposal_id>:<action>"`. Никаких данных, кроме id; всё остальное из БД.
8. Секреты только из `.env` через `Settings`. Токен бота нигде не логируем.
9. Персональные данные: храним только user_id/имя админов и согласие. Данные подписчиков канала не собираем. IP в кликах — только соль+хэш.
10. Всё, что зависит от внешних сервисов (MAX, LLM, эмбеддинги), — за интерфейсом с fake-реализацией для тестов.

## Стиль кода

- Типизация везде, `from __future__ import annotations`, pydantic-модели на границах.
- async-first. Никаких блокирующих вызовов в event loop (эмбеддинги — через `asyncio.to_thread`).
- Маленькие функции, понятные имена, docstring только там, где неочевидно «почему».
- Каждая фича — с тестом: юнит на логику (matching/policy/pricing) и интеграционный на сценарий с respx-моком MAX API и фикстурами Update в `tests/fixtures/updates/*.json`.

## Порядок работы (майлстоуны)

Делай строго по порядку, после каждого — зелёные тесты и короткий отчёт, что сделано и что проверить руками.

- **M0 — каркас:** структура, конфиг, клиент MAX API (`get_me`, `send_message`, `answer_callback`, `get_updates`, `get_chat`, `get_my_membership`, `get_messages`, `subscribe`), rate limiter, Dispatcher, polling-режим, эхо в личке.
- **M1 — онбординг:** `/start` → согласие (оферта + обработка данных) кнопкой; `bot_added` в канал → привязка к админу, проверка прав, инструкции при нехватке; `/settings` с блоклистом категорий.
- **M2 — подбор:** анализ поста LLM (JSON-схема), эмбеддинги, pgvector-поиск, policy-фильтры, скоринг, объяснение «почему эта реклама». Seed-каталог ~30 объявлений.
- **M3 — одобрение и публикация:** предложение в личку админу, 4 кнопки, TTL предложения, публикация в канал с маркировкой и кнопкой-ссылкой на трекер.
- **M4 — трекинг и деньги:** редирект `/r/{token}` с логом кликов, опрос просмотров, ledger с начислениями CPM/CPC/CPA (postback), `/stats` в боте.
- **M5 — демо:** webhook-режим за Caddy, страница «витрина рекламодателя» (read-only), скрипт демо-сценария.

Если время кончается — M5 урезается первым, M4 можно свести к кликам + ledger.
