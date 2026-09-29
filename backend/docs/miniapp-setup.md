# Локальный запуск и подключение мини-приложения MAX

## Требования

- Node.js 22 или новее и npm.
- Python 3.12, `uv`, PostgreSQL с pgvector — те же зависимости, что и у бота.
- Рабочие `MAX_BOT_TOKEN`, LLM-параметры и `DATABASE_URL` в `.env` для запуска backend.

## Локальная сборка

Из корня проекта соберите фронтенд, примените миграции и запустите существующее приложение:

```bash
npm ci && npm run build          # из корня репозитория → frontend/dist
cd backend
uv run alembic upgrade head
uv run python -m ctxads.run --mode polling
```

Готовая сборка обслуживается тем же FastAPI-процессом по `/miniapp/`, а API — по
`/api/miniapp/`. Отдельный MAX polling/webhook или отдельный worker для интерфейса не запускается.
Чтобы сохранить существующий сценарий запуска, откройте мини-приложение из MAX. Обычная вкладка
браузера и Vite dev server не получают подписанные данные запуска MAX; тестового обхода входа в
коде нет.

## На macOS без Docker: установленный PostgreSQL 16

Если PostgreSQL 16 уже установлен через Homebrew, установите `uv` и соберите pgvector для
**той же версии PostgreSQL**. Текущая формула `brew install pgvector` собирает расширение для
PostgreSQL 17/18, поэтому с PostgreSQL 16 используйте [официальную сборку из исходников](https://github.com/pgvector/pgvector#installation).

```bash
brew install uv
uv python install 3.12
git clone --depth 1 --branch v0.8.6 https://github.com/pgvector/pgvector.git /private/tmp/ctxads-pgvector-0.8.6
cd /private/tmp/ctxads-pgvector-0.8.6
export PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config
make
make install
brew services restart postgresql@16
pg_isready -h localhost -p 5432
```

Для стандартного `DATABASE_URL` из `.env.example` создайте роль `ctx` и базу `ctx` один раз.
Если они уже существуют, пропустите соответствующую команду:

```bash
psql postgres -c "CREATE ROLE ctx LOGIN PASSWORD 'ctx'"
createdb -O ctx ctx
```

Теперь вернитесь в папку проекта и запустите единственный процесс приложения. `uv` установит
зависимости Python 3.12; первая установка и загрузка модели эмбеддингов могут занять время.
Токен MAX и LLM-параметры уже должны быть заданы в `.env`.

```bash
cd /Users/mat2405mail.ru/Downloads/max
uv sync
uv run alembic upgrade head
uv run python -m ctxads.run --mode polling
```

Оставьте окно с ботом открытым. Проверка сервера: `http://localhost:8000/healthz` должна вернуть
`{"status":"ok"}`. Затем откройте чат бота в MAX и нажмите «Старт». Чтобы посмотреть именно
мини-приложение в MAX, выполните шаги раздела «Подключение в MAX» ниже: локальный HTTP-адрес
не передаст подписанные данные запуска и не подходит для настройки кнопки мини-приложения.
Для примеров рекламных предложений отдельно выполните `uv run python -m ctxads.seed`;
это добавит демонстрационные объявления в ту же базу.

Если проект работает в Docker, `docker compose ... --build` автоматически собирает фронтенд и
копирует результат в образ backend.

## Подключение в MAX

1. Разверните приложение на публичном HTTPS-домене. Mini App MAX требует HTTPS.
2. Укажите в `.env` тот же URL приложения с путём, например:

   ```dotenv
   MINIAPP_URL=https://bot.example.com/miniapp/
   ```

   Перезапустите backend после изменения конфигурации. `MINIAPP_URL` также добавляет кнопку
   запуска к ответам бота `/start`, `/help`, `/settings`, `/stats` и `/cabinet`.
3. В MAX для партнёров откройте **Чат-боты → нужный бот → Настройки**, укажите URL мини-приложения
   `https://bot.example.com/miniapp/`, выберите подпись кнопки и сохраните.
4. Откройте приложение из чата с ботом и проверьте согласие, список каналов и рекламный кабинет.

Настройки MAX принимают только валидный HTTPS URL. В боте и приложении используется один домен;
Caddy из текущего `compose.yaml` проксирует `/miniapp/` и API в тот же backend.

Документация платформы:

- [MAX Bridge](https://dev.max.ru/docs/webapps/bridge)
- [Проверка данных запуска](https://dev.max.ru/docs/webapps/validation)
- [MAX UI](https://dev.max.ru/ui)
- [Подключение мини-приложения в настройках бота](https://dev.max.ru/docs/webapps/introduction)

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `MAX_BOT_TOKEN` | Проверка подписи `initData` и текущий MAX Bot API; только backend, не фронтенд |
| `MINIAPP_URL` | Публичный HTTPS URL, который бот добавляет как кнопку открытия приложения |
| `DATABASE_URL` | Существующая PostgreSQL база с данными бота и мини-приложения |
| `WEBHOOK_PUBLIC_URL` | Как и прежде, адрес webhook MAX; это не URL мини-приложения |
| `PUBLIC_BASE_URL` | Существующий адрес трекера переходов по рекламным ссылкам |

Новая миграция `0004` создаёт таблицу завершённых мутаций мини-приложения для безопасного повтора
запросов. В Docker миграции применяются штатной командой запуска. При ручном запуске выполните
`uv run alembic upgrade head`.

## Проверка

```bash
cd backend
uv run pytest -q
uv run ruff check .
cd .. && npm run build   # из корня репозитория
```

Тесты с PostgreSQL используют `TEST_DATABASE_URL` (по умолчанию `ctx_test`). Они пропускаются, если
PostgreSQL или расширение pgvector недоступны. Для скриншотов и проверки клиента MAX требуется
открыть собранный HTTPS адрес из MAX; системная браузерная вкладка не может подделать этот запуск.
