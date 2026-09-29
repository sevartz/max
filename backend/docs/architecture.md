# Архитектура ctxads — MVP

## 1. Компоненты

```
                    ┌──────────────────── MAX ────────────────────┐
                    │  канал (бот = админ)      личка с админом    │
                    └──────┬───────────────────────────▲──────────┘
             Update (webhook/polling)                   │ POST /messages, /answers
                           │                            │
┌──────────────────────────▼────────────────────────────┴───────────────┐
│ app (FastAPI, один процесс)                                            │
│                                                                        │
│  transport/webhook.py ─┐                                               │
│  transport/polling.py ─┴─► dedupe ─► jobs(table) ─► worker loop        │
│                                                        │               │
│                                            bot/dispatcher.py           │
│                     ┌───────────────┬───────────────┬──┴───────────┐   │
│               onboarding       channel_events     posts        callbacks│
│                                                    │               │   │
│                                     matching/pipeline.py   ads/publisher│
│                          analyzer(LLM) → retriever(pgvector)           │
│                          → policy → scorer → explainer(LLM)            │
│                                                                        │
│  ads/tracking.py  GET /r/{token} → click log → 302                     │
│  ads/postback.py  GET /postback?click=…  (CPA)                         │
│  scheduler: publish_due, expire_proposals, poll_views                  │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │
                     PostgreSQL 16 + pgvector
```

Один процесс: FastAPI + фоновый воркер как asyncio-задачи в lifespan. Для хакатона этого достаточно; при росте воркер выносится в отдельный процесс без изменения кода (он читает ту же таблицу `jobs`).

## 2. Основные сценарии

### 2.1 Онбординг админа
1. Админ пишет боту `/start` (событие `bot_started` или `message_created` в личке).
2. Бот присылает текст оферты и согласия на обработку данных + кнопку «Принимаю». Пока согласия нет, бот ничего не делает с каналами этого пользователя.
3. Записываем `consents(user_id, version, accepted_at)`.
4. Бот объясняет: «Добавьте меня администратором канала с правами: читать все сообщения, публиковать».

### 2.2 Подключение канала
1. Приходит `bot_added` с `is_channel=true`, `chat_id`, `user` (кто добавил).
2. Если у `user` нет согласия → пишем ему в личку (если он стартовал бота) и ставим канал в статус `pending_consent`.
3. Проверяем права (`GET /chats/{id}/members/me`). Нет `read_all_messages` или `write` → статус `insufficient_rights`, инструкция админу.
4. Получаем инфо о канале (`GET /chats/{id}`: название, число подписчиков — поле проверить) и последние ~20 постов (`GET /messages?chat_id=`).
5. Строим **профиль канала**: LLM-резюме тематики + центроид эмбеддингов постов. Статус `active`.
6. `bot_admin_permissions_changed` → перепроверка прав; `bot_removed` → статус `removed`, отмена pending-предложений.

### 2.3 Новый пост → предложение
1. `message_created` в активном канале, отправитель не наш бот, пост не является нашей рекламой.
2. **Частотные ограничения** (policy, до LLM, чтобы не тратить вызовы):
   - прошло ≥ `min_posts_between_ads` постов с последней рекламы (по умолчанию 3);
   - прошло ≥ `min_minutes_between_ads` (по умолчанию 120);
   - не превышен `max_ads_per_day` (по умолчанию 3);
   - нет уже открытого предложения по этому каналу;
   - текст поста ≥ 80 символов (подписи к картинкам без текста пропускаем).
3. **Анализ поста** (`matching/analyzer.py`, 1 LLM-вызов, строгий JSON):
   ```json
   {
     "summary": "короткое резюме",
     "topics": ["питание", "диабет"],
     "category": "health",
     "intent": "информационный|покупательский|развлекательный",
     "brand_safety": "safe|sensitive|unsafe",
     "unsafe_reason": null
   }
   ```
   `unsafe` (трагедии, катастрофы, смерть, насилие, политика) → рекламу не предлагаем, логируем причину. Это отдельная фича для питча: реклама не появится под постом о трагедии.
4. **Поиск кандидатов** (`retriever.py`): эмбеддинг `summary + topics` → pgvector cosine top-20 по активным объявлениям.
5. **Фильтры** (`policy.py`):
   - категория не в блоклисте канала; если у канала задан allowlist — только из него;
   - регулируемые категории (`betting`, `alcohol`, `finance`, `medicine`) — только если канал явно их разрешил;
   - таргетинг объявления: разрешённые категории каналов, минимум подписчиков;
   - бюджет не исчерпан, объявление `active`;
   - это объявление не отклонялось в этом канале за последние 14 дней;
   - frequency cap: одно объявление не чаще 1 раза в 7 дней в канале.
6. **Скоринг** (`scorer.py`), без LLM:
   `score = 0.6 * sim(post, ad) + 0.25 * sim(channel_profile, ad) + 0.15 * norm(expected_payout)`.
   Порог `min_score` (по умолчанию 0.35): ниже — не предлагаем ничего. Честное «нет подходящей рекламы» лучше мусорной.
7. **Объяснение** (`explainer.py`, 1 LLM-вызов на топ-1): 1–2 предложения «почему эта реклама подходит к посту». Показывается админу — это главный вау-момент на демо.
8. **Предложение** — `proposals(status=pending, expires_at=now+2h)` + сообщение админу в личку:
   ```
   📌 Новый пост: «Бургер и сахар в крови…»
   💡 Предлагаем: Инвитро — анализ на глюкозу со скидкой 20%
   🧠 Почему: пост о влиянии фастфуда на уровень сахара, анализ — естественный следующий шаг для читателя
   💰 Модель: CPM 250 ₽ · ожидаемо ~180 ₽ за размещение
   [✅ Одобрить] [❌ Отклонить]
   [🔄 Другой вариант] [🚫 Не предлагать «Медицина»]
   ```

### 2.4 Реакция админа (`message_callback`, payload `p:<id>:<action>`)
- Сначала `POST /answers` (подтверждение нажатия), затем логика. Проверяем, что нажал владелец канала и предложение ещё `pending` и не истекло.
- `approve` → `proposals.status=approved`, job `publish` с `run_at = now + publish_delay` (по умолчанию 10 мин, чтобы реклама не шла вплотную к посту). Редактируем сообщение админу: «Одобрено, выйдет в 14:32».
- `reject` → `rejected`, запоминаем пару (канал, объявление).
- `next` → берём следующего кандидата из сохранённого топа (без повторного LLM-анализа), новое предложение, старое `superseded`. Не больше 3 «следующих» на пост.
- `block_cat` → категория добавляется в блоклист канала, предложение `rejected`.
- Истёкшие предложения job `expire_proposals` переводит в `expired` и убирает кнопки.

### 2.5 Публикация
1. Job `publish`: перепроверяем права бота, бюджет и частоту (могло измениться за время задержки).
2. Собираем пост (`ads/publisher.py`):
   ```
   <креатив рекламодателя>

   Реклама. ООО «Ромашка», ИНН 7700000000. erid: DEMO-xxxx
   [кнопка-ссылка: «Подробнее» → https://<host>/r/<placement_token>]
   ```
   Маркировка обязательна по закону о рекламе. В MVP ERID берётся из поля объявления (демо-значение), интеграция с ОРД — в roadmap.
3. `POST /messages?chat_id=` → сохраняем `placements(mid, published_at)`.
4. Опционально автоудаление через `ad_ttl_hours` (по умолчанию 48, нужно право `delete`; если его нет — не удаляем и пишем об этом в `/settings`).

### 2.6 Трекинг и деньги
- **Клик:** `GET /r/{token}` → `clicks(placement_id, ts, ip_hash, ua_hash)` → 302 на URL рекламодателя с `utm_*` и `click_id`. Антифрод MVP: один клик на ip_hash+placement за 24 ч.
- **Конверсия (CPA/реферальная модель):** `GET /postback?click_id=…&amount=…&sig=…` (HMAC-подпись секретом рекламодателя) → `conversions`.
- **Просмотры:** job `poll_views` каждые 30 мин для размещений младше 72 ч → `GET /messages` → обновить `placements.views` (поле статистики проверить в объекте Message).
- **Ledger** (`billing/`): каждое событие создаёт запись `ledger_entries(advertiser_debit, channel_credit, platform_fee)`:
  - CPM: при приросте просмотров, `price_per_1000 * Δviews / 1000`;
  - CPC: за уникальный клик;
  - CPA: процент или фикс от конверсии.
  Комиссия платформы — `PLATFORM_FEE` (например 0.3). Бюджет объявления уменьшается атомарно; при исчерпании → `paused`.
- Админу: `/stats` — размещения, просмотры, клики, заработано за 7/30 дней.

## 3. Модель данных (PostgreSQL)

| Таблица | Ключевые поля |
|---|---|
| `users` | max_user_id PK, name, created_at |
| `consents` | user_id, version, accepted_at |
| `channels` | chat_id PK, owner_user_id, title, subscribers, status (`pending_consent`/`insufficient_rights`/`active`/`paused`/`removed`), profile_summary, profile_embedding vector(384) |
| `channel_settings` | chat_id PK, blocked_categories text[], allowed_categories text[] null, allow_regulated text[], min_posts_between_ads, min_minutes_between_ads, max_ads_per_day, publish_delay_min, ad_ttl_hours |
| `posts` | id, chat_id, mid unique, text, created_at, analysis jsonb, embedding vector(384), is_our_ad bool |
| `advertisers` | id, name, legal_name, inn, postback_secret |
| `ads` | id, advertiser_id, title, body, url, image_url null, category, erid, pricing_model (`cpm`/`cpc`/`cpa`), price, budget_total, budget_left, targeting jsonb, status, embedding vector(384) |
| `proposals` | id, post_id, ad_id, chat_id, score, reason, candidates jsonb (оставшиеся id), status (`pending`/`approved`/`rejected`/`expired`/`superseded`/`published`), admin_message_mid, expires_at |
| `placements` | id, proposal_id, chat_id, mid, token unique, published_at, deleted_at, views |
| `clicks` | id, placement_id, click_id unique, ts, ip_hash, ua_hash |
| `conversions` | id, click_id, amount, ts |
| `ledger_entries` | id, placement_id, kind, advertiser_debit, channel_credit, platform_fee, ts |
| `jobs` | id, kind, payload jsonb, run_at, status, attempts, last_error |
| `processed_updates` | dedupe_key PK, ts |

Индексы: ivfflat/hnsw по `ads.embedding`; `posts(chat_id, created_at)`; `jobs(status, run_at)`.

## 4. Структура репозитория

```
ctxads/
├── frontend/                   # мини-приложение MAX (React + Vite + TypeScript) → frontend/dist
├── package.json                # сборка frontend (npm run build), package-lock.json
├── Dockerfile                  # frontend-build + backend в одном образе
├── compose.yaml                # db (pgvector/pgvector:pg16), app, caddy (профиль demo)
├── requirements.txt            # зависимости для pip (-e ./backend)
├── .env.example
└── backend/
    ├── PROJECT_GUIDE.md
    ├── docs/                   # architecture, SETUP, demo, frontend-ux, miniapp-*
    ├── pyproject.toml          # + uv.lock
    ├── Caddyfile               # HTTPS с Let's Encrypt для webhook
    ├── alembic/
    ├── seed/ads.yaml           # ~30 демо-объявлений по 10 категориям
    ├── src/ctxads/
    │   ├── config.py               # Settings (pydantic-settings)
    │   ├── run.py                  # CLI: --mode polling|webhook
    │   ├── app.py                  # FastAPI factory, lifespan (воркер, планировщик)
    │   ├── max_api/
    │   │   ├── client.py           # MaxClient: методы API, retry, rate limit
    │   │   ├── models.py           # Update, Message, User, Chat, Button (pydantic, extra="allow")
    │   │   └── errors.py
    │   ├── transport/
    │   │   ├── webhook.py          # POST /webhook/max
    │   │   └── polling.py
    │   ├── bot/
    │   │   ├── dispatcher.py       # update_type → handler
    │   │   ├── handlers/{onboarding,channel,posts,callbacks,settings,stats,cabinet}.py
    │   │   ├── keyboards.py
    │   │   └── texts.py
    │   ├── matching/
    │   │   ├── analyzer.py  retriever.py  policy.py  scorer.py  explainer.py
    │   │   └── pipeline.py         # собирает всё: post → Proposal | None (+ причина)
    │   ├── llm/{base,openai_compat,yandexgpt,gigachat,fake}.py   # openai_compat = NVIDIA NIM
    │   ├── embeddings/{base,e5_local,fake}.py
    │   ├── ads/{publisher,tracking,postback,stats,showcase,cabinet,channel_settings,proposals}.py
    │   ├── miniapp/{api,auth}.py        # API мини-приложения /api/miniapp/*, проверка подписи MAX
    │   ├── billing/{pricing,ledger}.py
    │   ├── jobs/{queue,worker,scheduler}.py
    │   ├── db/{session,models}.py + db/repo/*.py
    │   └── seed.py
    └── tests/
        ├── fixtures/updates/*.json # bot_added, message_created (канал/личка), message_callback
        ├── unit/                   # policy, scorer, pricing, publisher (формат маркировки)
        └── integration/            # сценарии через Dispatcher + respx-мок MAX + FakeLLM
```

## 5. Конфиг (.env.example)

```
MAX_BOT_TOKEN=
MAX_API_BASE=https://platform-api2.max.ru
WEBHOOK_PUBLIC_URL=https://example.com/webhook/max
WEBHOOK_SECRET=change-me-strong-secret
PUBLIC_BASE_URL=https://example.com          # для /r/{token}
DATABASE_URL=postgresql+asyncpg://ctx:ctx@localhost:5432/ctx
LLM_PROVIDER=openai_compat                    # openai_compat (NVIDIA NIM)|yandexgpt|gigachat|fake
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_API_KEY=nvapi-...                         # ключ с build.nvidia.com
LLM_MODEL=                                    # имя модели из каталога NIM (Qwen/DeepSeek)
LLM_RPM=30                                    # клиентский лимит (у бесплатного NIM ~40 RPM)
LLM_FALLBACK_PROVIDER=                        # опционально: yandexgpt|gigachat
LLM_FOLDER_ID=                                # только для YandexGPT
EMBEDDER=e5_local                             # e5_local|fake
PLATFORM_FEE=0.3
PROPOSAL_TTL_MIN=120
MIN_MATCH_SCORE=0.35
CLICK_SALT=change-me
```

## 6. Решения и почему

- **Свой клиент вместо сторонней библиотеки:** нужно ~10 методов, а контроль над rate limit, ретраями и новыми полями важнее. Модели с `extra="allow"`, чтобы новые поля API не ломали парсинг.
- **Jobs в Postgres, без Redis:** на один компонент меньше, задачи переживают рестарт, отложенная публикация — это просто `run_at`.
- **LLM через NVIDIA NIM на время хакатона:** бесплатно, без карты, OpenAI-совместимый API, доступны сильные открытые модели (Qwen, DeepSeek). Ограничения бесплатного тарифа (~1000 вызовов, ~40 RPM) закрываются кэшем анализа, лимитером, ретраями и `FakeLLM` в тестах. В LLM уходит только публичный текст постов.
- **Прод — российская LLM (YandexGPT/GigaChat):** в жюри госорганы и VK, вопрос о том, куда уходят данные, будет. Ответ: провайдер меняется одной переменной `LLM_PROVIDER`, код не трогаем.
- **Скоринг без LLM, LLM только для анализа и объяснения:** 2 вызова на пост, предсказуемая стоимость и латентность, скоринг легко тестировать.
- **Порог релевантности:** лучше ничего не предложить, чем предложить мусор. Иначе админ перестанет открывать предложения.
- **Задержка публикации после одобрения:** реклама не должна идти сразу за постом; плюс окно, чтобы админ передумал (кнопка «Отменить» в сообщении об одобрении — nice to have).

## 7. Демо-сценарий (для питча, 3 минуты)

1. Тестовый канал про ЗОЖ, бот уже админ.
2. Публикуем пост «Как бургер влияет на сахар в крови».
3. Через ~5 сек админу приходит предложение «Инвитро — анализ на глюкозу» с объяснением.
4. Жмём «🚫 Не предлагать Медицину» → следующий пост про спорт → приходит предложение спорттоваров, медицины больше нет.
5. Публикуем пост о трагедии → бот ничего не предлагает, в логах `brand_safety=unsafe`.
6. Одобряем → рекламный пост с маркировкой в канале → кликаем → `/stats` показывает клик и начисление.

## 8. Roadmap после хакатона (для слайда, не для MVP)

- Интеграция с ОРД и реальные ERID.
- Кабинет рекламодателя: загрузка креативов, бюджеты, оплата.
- SDK/middleware для владельцев ботов: реклама в ответах любых ботов MAX (не только ИИ).
- Автоодобрение по правилам для доверенных рекламодателей.
- Мини-приложение MAX для админа вместо команд в чате.

## 9. Что проверить в документации до реализации (TODO verify-api)

- Схема тела `POST /answers` (ответ на callback) и можно ли через неё редактировать исходное сообщение.
- Поля объекта `Message`: где `sender`, `body.text`, `body.mid`, статистика просмотров постов канала.
- Поле числа подписчиков в объекте `Chat`.
- Пагинация `GET /messages` для канала (параметры `count`, `from`/`to`).
- Структура `message_callback` Update: где `callback_id`, `payload`, `user`.
