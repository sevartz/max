# Запуск ctxads — инструкция для человека и для ИИ-агента

> **Структура репозитория:** Python-код — в `backend/`, мини-приложение — в `frontend/`, `.env` — в корне.
> Команды `uv`, `alembic`, `pytest`, `ruff` и `python -m ctxads.*` выполняйте **из папки `backend/`**;
> `npm`, `docker compose` и `cp .env.example .env` — из корня.

Бот-администратор канала в MAX: читает новый пост, подбирает под него рекламу из каталога,
присылает админу в личку предложение с кнопками, после одобрения публикует рекламу в канал
с маркировкой и считает клики и просмотры.

- Что и зачем строим, правила кода: [CLAUDE.md](../CLAUDE.md)
- Архитектура: [docs/architecture.md](architecture.md)
- Сценарий показа: [docs/demo.md](demo.md)

Статус на 27.09.2026: реализованы все этапы M0–M5, 109 тестов, `ruff` без замечаний.
Перед началом работы прочитайте раздел «Известные проблемы» в конце.

---

## 1. Что нужно установить

| Что | Зачем |
|---|---|
| Python **3.12** (ровно 3.12, не 3.13) | так указано в `pyproject.toml` |
| [uv](https://docs.astral.sh/uv/) *или* обычный `pip` | установка зависимостей |
| PostgreSQL 16+ с расширением **pgvector** | база и векторный поиск |
| ~1 ГБ на диске | torch и модель эмбеддингов `multilingual-e5-small` (~120 МБ, скачается при первом запуске) |

## 2. Ключи и конфиг

Все секреты хранятся **только в `.env`** в корне проекта. `.env.example` — шаблон,
ключи туда не пишите: бот его не читает, а сам файл может попасть в репозиторий.

```bash
cp .env.example .env
```

Обязательные поля в `.env`:

| Переменная | Что это |
|---|---|
| `MAX_BOT_TOKEN` | токен бота MAX |
| `LLM_API_KEY` | ключ NVIDIA NIM (`nvapi-...`) с build.nvidia.com |
| `LLM_MODEL` | модель из каталога NIM, например `deepseek-ai/deepseek-v4.1-flash` |
| `DATABASE_URL` | по умолчанию `postgresql+asyncpg://ctx:ctx@localhost:5432/ctx` |
| `PUBLIC_BASE_URL` | локально `http://localhost:8000`; для рабочих ссылок на трекер нужен публичный адрес |

Остальные поля можно не менять. `WEBHOOK_*` нужны только в режиме webhook (раздел 7).
Для работы без внешних сервисов есть `LLM_PROVIDER=fake` и `EMBEDDER=fake`.

## 3. База данных

Нужны две базы: `ctx` для бота и `ctx_test` для тестов. В обеих нужно расширение `vector`.

### Вариант А: Docker

```bash
docker compose up -d db
docker compose exec db createdb -U ctx ctx_test
```

### Вариант Б: локальный Postgres на macOS (Homebrew)

```bash
brew install postgresql@18 pgvector
LC_ALL=en_US.UTF-8 /opt/homebrew/opt/postgresql@18/bin/pg_ctl -D /opt/homebrew/var/postgresql@18 start
psql -d postgres -c "CREATE ROLE ctx LOGIN PASSWORD 'ctx' SUPERUSER;"
createdb -O ctx ctx
createdb -O ctx ctx_test
```

> `LC_ALL=en_US.UTF-8` обязателен: без него Postgres 18 на macOS падает при старте с ошибкой
> `postmaster became multithreaded during startup`. Роль `SUPERUSER` нужна только для локальной
> разработки, чтобы миграции и тесты могли выполнить `CREATE EXTENSION vector`.

## 4. Установка и подготовка

### Через uv (рекомендуется)

```bash
uv sync                          # зависимости из uv.lock, включая dev
uv run alembic upgrade head      # миграции
uv run python -m ctxads.seed     # 30 демо-объявлений (повторный запуск безопасен: upsert)
```

### Через pip

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt   # из корня; или requirements.txt без инструментов разработки
.venv/bin/alembic upgrade head
.venv/bin/python -m ctxads.seed
```

Дальше в инструкции команды даны в варианте `uv run ...`. С pip вместо `uv run` пишите `.venv/bin/`
(например, `.venv/bin/python -m ctxads.run --mode polling`).

> Ставьте через `pip`, а не `uv pip install -r requirements.txt`: из-за дополнительного индекса
> PyTorch `uv pip` не находит часть пакетов (например, `certifi`).

## 5. Проверка без MAX и без ключей

```bash
uv run pytest -q                 # 109 тестов, в сеть не ходят (FakeLLM, мок MAX)
uv run ruff check .
uv run python -m ctxads.demo     # весь сценарий офлайн: FakeLLM + настоящие e5 и pgvector
```

Демо печатает, что увидели бы админ и канал:

1. онбординг;
2. пост про бургер → предложение «анализ на глюкозу»;
3. блок категории «Медицина»;
4. пост о трагедии → рекламы нет (`unsafe`);
5. пост про бег → одобрение → публикация с маркировкой;
6. клик → `/stats`.

`uv run python -m ctxads.demo --llm env` — то же самое с настоящей NIM из `.env` (≈6 кредитов).

## 6. Запуск с настоящим ботом (локально, long polling)

```bash
uv run python -m ctxads.run --mode polling
```

Ручная проверка в MAX:

1. Напишите боту `/start` → «Принимаю».
2. Добавьте бота **администратором канала** с правами «Читать все сообщения» и «Публиковать сообщения».
   Право «Удалять сообщения» по желанию: с ним бот удаляет рекламу через 48 ч.
   Бот ответит в личку «Канал подключён» или напишет, каких прав не хватает.
3. Опубликуйте в канале пост **не короче 80 символов** на «рекламную» тему (спорт, еда, техника…).
4. Через несколько секунд в личку придёт предложение с кнопками.
5. «Одобрить» → реклама выйдет в канал примерно через **1 минуту** (`publish_delay_min`).
6. Команды в личке: `/settings` (блок категорий, регулируемые категории, пауза), `/stats`, `/help`.

Кабинет рекламодателя (тот же бот, можно с того же аккаунта):

1. `/cabinet` → название компании → меню кабинета.
2. «➕ Новое объявление»: заголовок → текст → ссылка → категория (кнопки) → модель оплаты
   CPM/CPC/CPA (кнопки) → цена → бюджет → превью → «✅ Запустить».
3. Объявление сразу участвует в подборе. Опубликуйте в канале пост на его тему — бот должен
   предложить именно его.
4. В карточке объявления: правка полей, «⏸ Приостановить» (на паузе в подбор не попадает),
   «➕ Пополнить бюджет» (демо, без оплаты). «📊 Статистика» — сводка по всем объявлениям.
5. `/cancel` или любая другая команда прерывает пошаговый ввод.

Почему бот может промолчать на пост (это ожидаемое поведение, причина пишется в `posts.skip_reason`):

- пост короче 80 символов;
- уже есть открытое предложение;
- с прошлой рекламы прошло меньше 3 постов или меньше 120 минут; лимит — 3 рекламы в сутки;
- пост небезопасен для рекламы (`unsafe`), подходящих объявлений нет (`no_match`),
  или LLM недоступна (`llm_unavailable`).

```bash
psql -d ctx -c "select mid, skip_reason, left(text,60) from posts order by id desc limit 10;"
```

Если polling не получает событий, скорее всего у бота активна webhook-подписка: пока она есть,
long polling в MAX не работает.

Эндпоинты HTTP-сервера (порт 8000): `/healthz`, `/r/{token}` (трекер клика),
`/postback` (конверсия CPA), `/showcase` (витрина рекламодателя).

## 7. Webhook-режим на сервере (демо и прод)

MAX принимает webhook только по HTTPS на порту 443 с сертификатом доверенного CA.
В `compose.yaml` для этого есть Caddy с Let's Encrypt.

В `.env`:

```
WEBHOOK_PUBLIC_URL=https://<домен>/webhook/max
PUBLIC_BASE_URL=https://<домен>
WEBHOOK_SECRET=<openssl rand -hex 32>
CLICK_SALT=<openssl rand -hex 16>
```

`WEBHOOK_PUBLIC_URL` и `WEBHOOK_SECRET` обязательны: без них бот в режиме webhook не стартует.
Без секрета кто угодно мог бы слать боту поддельные события.

```bash
DOMAIN=<домен> docker compose --profile demo up -d --build
docker compose exec app uv run --no-sync python -m ctxads.seed
```

При старте бот сам вызывает `POST /subscriptions`, миграции применяются автоматически.

Снаружи открыты только порты 80 и 443 (Caddy). Postgres (5432) и приложение (8000)
опубликованы только на `127.0.0.1` сервера. Домен должен указывать на сервер (A-запись),
порты 80 и 443 должны быть открыты: Let's Encrypt выдаёт сертификат через них.

---

## 8. Для ИИ-агента

**Прежде чем менять код, прочитай [CLAUDE.md](../CLAUDE.md) и [docs/architecture.md](architecture.md).**
Там стек, архитектурные правила, проверенные факты о MAX Bot API и порядок работы.

Жёсткие правила (из CLAUDE.md, коротко):

- Тесты и CI **никогда** не ходят в NIM и MAX: только `FakeLLM`, `FakeEmbedder`, respx-мок MAX.
  У NIM ~1000 бесплатных кредитов, их тратят только живые посты.
- Не выдумывай эндпоинты и поля MAX API. Если не можешь проверить на dev.max.ru —
  пиши `# TODO(verify-api): …` и делай код устойчивым к отсутствию поля.
- Webhook-хендлер только проверяет secret, дедуплицирует и кладёт событие в `jobs`.
  LLM и сеть вызываются только в воркере.
- Все пользовательские тексты — в `src/ctxads/bot/texts.py`. SQL — только в `db/repo/`.
- Секреты только из `.env` через `Settings`. Токен бота не логировать.
- Каждая фича — с тестом: юнит на логику, интеграционный на сценарий с фикстурами
  `tests/fixtures/updates/*.json`.

Карта кода (`src/ctxads/`):

| Папка | Что внутри |
|---|---|
| `max_api/` | HTTP-клиент MAX, модели Update/Message, ошибки |
| `transport/` | polling, webhook, `ingest` (дедупликация и постановка в очередь) |
| `jobs/` | очередь в Postgres (`FOR UPDATE SKIP LOCKED`), воркер, планировщик, реестр задач |
| `bot/` | `Dispatcher`, хендлеры сценариев (в т.ч. `handlers/cabinet.py` — кабинет рекламодателя), клавиатуры, тексты |
| `matching/` | анализ поста LLM, поиск в pgvector, policy-фильтры, скоринг, объяснение |
| `ads/` | публикация, трекинг кликов, postback, статистика просмотров, витрина, `cabinet.py` — логика кабинета |
| `billing/` | цены CPM/CPC/CPA, ledger |
| `llm/`, `embeddings/` | интерфейсы + реализации (NIM, YandexGPT, GigaChat, fake; e5, fake) |
| `db/` | модели SQLAlchemy, сессии, репозитории (`repo/dialogs.py` — шаги пошагового ввода) |

После обновления кода всегда выполняйте `uv run alembic upgrade head`: миграция `0002` добавляет
владельца рекламодателя и таблицу `dialog_states` для кабинета.

Проверка после любых изменений:

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check .
```

Если меняются зависимости — правь `pyproject.toml`, затем `uv lock` и перегенерируй requirements:

```bash
uv export --format requirements-txt --no-hashes --no-dev --no-header -o ../requirements.txt   # из backend/
```

После перегенерации замени в `requirements.txt` строку `-e .` на `-e ./backend` и верни в начало строку
`--extra-index-url https://download.pytorch.org/whl/cpu` и шапку-комментарий.
`backend/requirements-dev.txt` перегенерируй так же: `--only-dev --no-emit-project`, сверху `-r ../requirements.txt`.

---

## 9. Известные проблемы (на 27.09.2026)

1. **Два теста падают после 25.09.2026** (`test_polling_app_runs_worker_scheduler_and_poller`,
   `test_worker_runs_update_job_end_to_end`). Часы в тестах зафиксированы на
   `2026-09-25 09:00 UTC` (`Clock` в `tests/conftest.py`), а `jobs.run_at` проставляет Postgres
   через `now()` по реальному времени. Воркер считает задачу «будущей» и не берёт её.
   Если сдвинуть часы к реальному времени, проходят все 109. Ошибка в тестах, в самом боте её нет.
2. **Риск повторной публикации рекламы** (`ads/publisher.py`). Сначала пост уходит в канал,
   потом статус пишется в БД. Если запись упадёт, задача `publish` повторится, и реклама
   выйдет в канал второй раз. Нужна пометка «отправляется» до отправки и отказ от повтора
   после успешной отправки.
3. **Демо пишет в рабочую базу `ctx`** и оставляет там данные (каналы, предложения, задачи).
   Незавершённый запуск может оставить задачу `publish`, которую потом подхватит воркер.
   Очистка: `psql -d ctx -c "delete from jobs where status in ('pending','failed');"`.
4. **Не проверено по документации MAX** (`TODO(verify-api)` в коде): поле `notification`
   в `POST /answers`, параметры `DELETE /messages`, формат `message_ids` в `GET /messages`,
   структура `Callback`, картинка в рекламном посте по `url`. Проверить на живом боте.
5. Если `PUBLIC_BASE_URL` локальный (`localhost`), MAX не принимает ссылку на трекер в кнопке,
   поэтому «Подробнее» ведёт сразу на ссылку рекламодателя (с utm-метками), а клики не считаются.
   Для подсчёта кликов нужен публичный HTTPS-адрес: туннель или сервер.
6. В MVP замоканы: ERID (демо-значения из `seed/ads.yaml`, без ОРД), платежи (ledger только
   считает), рекламодатели и ИНН в seed вымышленные.
