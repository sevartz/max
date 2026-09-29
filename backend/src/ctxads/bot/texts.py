"""Все пользовательские тексты бота. В хендлерах строк нет — только ссылки сюда."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from ctxads.matching.categories import label

TZ = ZoneInfo("Europe/Moscow")

PRICING_LABELS = {"cpm": "CPM", "cpc": "CPC", "cpa": "CPA"}
PRICING_UNITS = {"cpm": "за 1000 показов", "cpc": "за клик", "cpa": "за целевое действие"}

CONSENT = (
    "👋 Я помогаю админам каналов MAX зарабатывать на рекламе, которая подходит "
    "к каждому конкретному посту.\n\n"
    "Как это работает:\n"
    "1. Вы добавляете меня администратором канала.\n"
    "2. Когда выходит пост, я подбираю к нему подходящее объявление и присылаю вам.\n"
    "3. Вы решаете: одобрить, отклонить или попросить другой вариант. Без вашего «да» "
    "ничего не публикуется.\n\n"
    "Перед началом примите условия:\n"
    "• Оферта: размещение рекламы в канале, доход — доля от оплаты рекламодателя "
    "(70% каналу, 30% платформе).\n"
    "• Согласие на обработку данных: храним только ваш ID в MAX, имя и факт согласия. "
    "Данные подписчиков канала не собираем. В LLM уходит только публичный текст постов."
)
CONSENT_BUTTON = "✅ Принимаю"
CONSENT_ACCEPTED = (
    "Спасибо! Условия приняты.\n\n"
    "Теперь добавьте меня администратором своего канала с правами:\n"
    "• «Читать все сообщения»\n"
    "• «Публиковать сообщения»\n"
    "По желанию — «Удалять сообщения», чтобы я убирал рекламу через 48 часов.\n\n"
    "Команды: /settings — настройки каналов, /stats — статистика, /help — помощь."
)
CONSENT_ALREADY = "Условия уже приняты. /settings — настройки каналов, /stats — статистика."
CONSENT_REQUIRED_FOR_CHANNEL = (
    "Меня добавили в канал «{title}», но сначала нужно принять условия. Нажмите /start."
)

HELP = (
    "Команды:\n"
    "/start — условия и подключение\n"
    "/settings — какие категории рекламы разрешены в ваших каналах\n"
    "/stats — размещения, просмотры, клики и доход\n"
    "/cabinet — кабинет рекламодателя: свои объявления, бюджет, статистика\n"
    "/cancel — прервать ввод\n\n"
    "Я подбираю рекламу к каждому новому посту и присылаю предложение сюда. "
    "Публикую только после вашего одобрения."
)

CHANNEL_CONNECTED = (
    "✅ Канал «{title}» подключён.\n"
    "Подписчиков: {subscribers}. Профиль: {profile}\n\n"
    "Когда выйдет новый пост, я пришлю предложение рекламы к нему."
)
CHANNEL_NO_RIGHTS = (
    "⚠️ Канал «{title}»: не хватает прав ({missing}).\n"
    "Откройте настройки канала → Администраторы → бот и включите: "
    "«Читать все сообщения» и «Публиковать сообщения». Я перепроверю права автоматически."
)
CHANNEL_REMOVED = "Меня удалили из канала «{title}». Открытые предложения отменены."
PROFILE_UNKNOWN = "соберу по новым постам"
PERMISSION_LABELS = {
    "read_all_messages": "читать все сообщения",
    "write": "публиковать сообщения",
}


def untitled(chat_id: int) -> str:
    return f"канал {chat_id}"


def rub(amount: Decimal | int) -> str:
    amount = Decimal(amount)
    q = amount.quantize(Decimal("1")) if amount >= 10 else amount.quantize(Decimal("0.01"))
    return f"{q:,}".replace(",", " ") + " ₽"


def post_preview(text: str, limit: int = 60) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1].rstrip() + "…"


def proposal(
    *,
    post_text: str,
    channel_title: str,
    ad_title: str,
    reason: str,
    pricing_model: str,
    price: Decimal,
    expected: Decimal,
    expires_at: datetime,
) -> str:
    return (
        f"📌 Новый пост в «{channel_title}»: «{post_preview(post_text)}»\n"
        f"💡 Предлагаем: {ad_title}\n"
        f"🧠 Почему: {reason}\n"
        f"💰 Модель: {PRICING_LABELS.get(pricing_model, pricing_model)} {rub(price)} "
        f"{PRICING_UNITS.get(pricing_model, '')} · ожидаемо ~{rub(expected)} за размещение\n"
        f"⏳ Предложение действует до {fmt_time(expires_at)}"
    )


BTN_APPROVE = "✅ Одобрить"
BTN_REJECT = "❌ Отклонить"
BTN_NEXT = "🔄 Другой вариант"


def btn_block_category(category: str) -> str:
    return f"🚫 Не предлагать «{label(category)}»"


def fmt_time(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%H:%M")


def approved(base: str, publish_at: datetime) -> str:
    return f"{base}\n\n✅ Одобрено, выйдет в {fmt_time(publish_at)}."


def rejected(base: str) -> str:
    return f"{base}\n\n❌ Отклонено. Это объявление не буду предлагать в канале 14 дней."


def category_blocked(base: str, category: str) -> str:
    return (
        f"{base}\n\n🚫 Категория «{label(category)}» больше не предлагается. /settings — вернуть."
    )


def superseded_no_more(base: str) -> str:
    return f"{base}\n\nДругих подходящих вариантов к этому посту нет."


def expired(base: str) -> str:
    return f"{base}\n\n⌛ Предложение истекло."


def cancelled(base: str) -> str:
    return f"{base}\n\nПредложение отменено."


def published(title: str, channel_title: str) -> str:
    return f"📣 Реклама «{title}» опубликована в «{channel_title}»."


def publish_failed(title: str, reason: str) -> str:
    return f"Не удалось опубликовать «{title}»: {reason}."


PUBLISH_FAIL_RIGHTS = "у бота нет права публиковать в канале"
PUBLISH_FAIL_BUDGET = "у рекламодателя закончился бюджет"
PUBLISH_FAIL_FREQUENCY = "превышен дневной лимит рекламы в канале"
PUBLISH_FAIL_CHANNEL = "канал отключён"

ACK_OK = "Готово"
ACK_NOT_OWNER = "Решение принимает владелец канала"
ACK_STALE = "Предложение уже неактуально"
ACK_WORKING = "Ищу другой вариант…"
ACK_CAB_STALE = "Кнопка устарела — откройте /cabinet"

AD_MARKING = "#Реклама. {legal_name}, ИНН {inn}. erid: {erid}"
AD_MARKING_NO_INN = "#Реклама. {legal_name}. erid: {erid}"
AD_BUTTON = "Подробнее"

SETTINGS_NO_CHANNELS = (
    "Пока нет подключённых каналов. Добавьте меня администратором канала — и он появится здесь."
)
SETTINGS_PICK = "Выберите канал:"
STATUS_LABELS = {
    "active": "🟢 активен",
    "paused": "⏸ на паузе",
    "pending_consent": "⏳ ждёт согласия",
    "insufficient_rights": "⚠️ не хватает прав",
    "removed": "удалён",
}


def settings_channel(
    title: str,
    status: str,
    blocked: list[str],
    regulated_allowed: list[str],
    can_delete: bool,
    ttl_hours: int,
) -> str:
    blocked_s = ", ".join(label(c) for c in blocked) or "нет"
    reg_s = ", ".join(label(c) for c in regulated_allowed) or "нет"
    delete_s = (
        f"удаляю рекламу через {ttl_hours} ч"
        if can_delete
        else "нет права «Удалять сообщения» — реклама не удаляется автоматически"
    )
    return (
        f"⚙️ «{title}» — {STATUS_LABELS.get(status, status)}\n"
        f"Заблокированные категории: {blocked_s}\n"
        f"Разрешённые регулируемые: {reg_s}\n"
        f"Автоудаление: {delete_s}\n\n"
        "Нажмите на категорию, чтобы запретить/разрешить её. "
        "Регулируемые (⚖️) по умолчанию выключены."
    )


def settings_category_button(category: str, blocked: bool, regulated: bool, allowed: bool) -> str:
    if regulated:
        enabled = allowed and not blocked
        return f"{'✅' if enabled else '⚖️'} {label(category)}"
    return f"{'🚫' if blocked else '✅'} {label(category)}"


BTN_PAUSE = "⏸ Приостановить"
BTN_RESUME = "▶️ Возобновить"
BTN_BACK = "← К списку каналов"

STATS_EMPTY = "Пока нет подключённых каналов."


def stats_block(title: str, days: int, st: dict[str, object]) -> str:
    return (
        f"«{title}» за {days} дн.: размещений {st['placements']}, просмотров {st['views']}, "
        f"кликов {st['clicks']}, заработано {rub(st['earned'])}"  # type: ignore[arg-type]
    )


UNKNOWN_COMMAND = "Не понял команду. /help — список команд."

# --- Кабинет рекламодателя ---

CAB_ASK_COMPANY = (
    "💼 Кабинет рекламодателя.\n\n"
    "Здесь вы создаёте объявления, а я предлагаю их админам каналов под подходящие посты. "
    "Платите только за результат: показы (CPM), клики (CPC) или целевые действия (CPA).\n\n"
    "Как называется ваша компания или бренд? Это название будет в маркировке рекламы."
)
CAB_BAD_COMPANY = "Название — от 1 до 100 символов. Попробуйте ещё раз или /cancel."
CAB_ASK_TITLE = "Шаг 1/7. Заголовок объявления (до 100 символов):"
CAB_ASK_BODY = "Шаг 2/7. Текст объявления (до 1000 символов):"
CAB_ASK_URL = "Шаг 3/7. Ссылка, куда вести читателей (https://…):"
CAB_ASK_CATEGORY = "Шаг 4/7. Категория — по ней я подбираю подходящие посты:"
CAB_ASK_MODEL = "Шаг 5/7. За что платите:"
CAB_ASK_BUDGET = "Шаг 7/7. Бюджет объявления, ₽ (не меньше цены). Пополнение демо, без оплаты:"
CAB_BAD_TITLE = "Заголовок — от 1 до 100 символов. Попробуйте ещё раз:"
CAB_BAD_BODY = "Текст — от 1 до 1000 символов. Попробуйте ещё раз:"
CAB_BAD_URL = "Нужна ссылка вида https://example.com. Попробуйте ещё раз:"
CAB_BAD_MONEY = "Нужна сумма в рублях, например 250 или 1500,50. Попробуйте ещё раз:"
CAB_BAD_BUDGET = "Бюджет не может быть меньше цены ({price}). Попробуйте ещё раз:"
CAB_USE_BUTTONS = "Выберите вариант кнопкой выше или /cancel."
CAB_CANCELLED = "Ввод отменён. /cabinet — вернуться в кабинет."
CAB_NOTHING_TO_CANCEL = "Нечего отменять. /help — список команд."
CAB_NO_ADS = "Объявлений пока нет — создайте первое."
CAB_ASK_TOPUP = "На сколько пополнить бюджет «{title}», ₽? (демо, без оплаты)"
CAB_TOPPED_UP = "✅ Бюджет «{title}» пополнен на {amount}."
CAB_SAVED = "✅ Сохранено."
CAB_EDIT_PROMPTS = {
    "title": "Новый заголовок (до 100 символов):",
    "body": "Новый текст объявления (до 1000 символов):",
    "url": "Новая ссылка (https://…):",
    "price": "Новая цена, ₽:",
}
CAB_ASK_NEW_CATEGORY = "Новая категория:"

BTN_CAB_NEW = "➕ Новое объявление"
BTN_CAB_STATS = "📊 Статистика"
BTN_CAB_LAUNCH = "✅ Запустить"
BTN_CAB_CANCEL = "❌ Отмена"
BTN_CAB_BACK = "← К объявлениям"
BTN_CAB_TOPUP = "➕ Пополнить бюджет"
BTN_CAB_EDIT = {
    "title": "✏️ Заголовок",
    "body": "✏️ Текст",
    "url": "✏️ Ссылка",
    "category": "🏷 Категория",
    "price": "💰 Цена",
}

CAB_STATUS_LABELS = {
    "active": "🟢 показывается",
    "paused": "⏸ на паузе",
    "exhausted": "⚠️ бюджет исчерпан",
}


def pricing(model: str, price: Decimal) -> str:
    return f"{PRICING_LABELS.get(model, model)} {rub(price)} {PRICING_UNITS.get(model, '')}"


def cab_pricing_button(model: str) -> str:
    return f"{PRICING_LABELS[model]} — {PRICING_UNITS[model]}"


def cab_ask_price(model: str) -> str:
    return f"Шаг 6/7. Цена {PRICING_LABELS[model]}, ₽ {PRICING_UNITS[model]}:"


def cab_menu(name: str, total: int, active: int, budget_left: Decimal) -> str:
    return (
        f"💼 Кабинет рекламодателя «{name}»\n"
        f"Объявлений: {total}, показываются: {active}. Остаток бюджета: {rub(budget_left)}\n\n"
        "Выберите объявление или создайте новое."
    )


def cab_ad_button(status: str, title: str) -> str:
    return f"{CAB_STATUS_LABELS.get(status, status).split(' ', 1)[0]} {post_preview(title, 40)}"


def cab_preview(draft: dict[str, str], company: str) -> str:
    price = pricing(draft["pricing_model"], Decimal(draft["price"]))
    return (
        "Проверьте объявление — так оно выглядит в канале:\n\n"
        f"{draft['title']}\n\n{draft['body']}\n\n"
        f"#Реклама. {company}. erid: присвоим при запуске\n\n"
        f"🔗 {draft['url']}\n"
        f"🏷 {label(draft['category'])} · 💰 {price}\n"
        f"Бюджет: {rub(Decimal(draft['budget']))}"
    )


def cab_launched(title: str) -> str:
    return (
        f"🚀 Объявление «{title}» запущено. Буду предлагать его админам каналов, "
        "когда выйдет подходящий пост."
    )


def cab_ad_card(
    *,
    title: str,
    body: str,
    url: str,
    category: str,
    pricing_model: str,
    price: Decimal,
    status: str,
    budget_left: Decimal,
    budget_total: Decimal,
    st: dict[str, object],
) -> str:
    return (
        f"📄 {title}\n{body}\n\n"
        f"🔗 {url}\n"
        f"🏷 {label(category)} · 💰 {pricing(pricing_model, price)}\n"
        f"Статус: {CAB_STATUS_LABELS.get(status, status)}\n"
        f"Бюджет: осталось {rub(budget_left)} из {rub(budget_total)}\n"
        f"Размещений {st['placements']} · просмотров {st['views']} · кликов {st['clicks']} · "
        f"потрачено {rub(st['spent'])}"  # type: ignore[arg-type]
    )


def cab_stats(name: str, lines: list[str], totals: dict[str, object]) -> str:
    head = (
        f"📊 «{name}»: размещений {totals['placements']}, просмотров {totals['views']}, "
        f"кликов {totals['clicks']}, потрачено {rub(totals['spent'])}"  # type: ignore[arg-type]
    )
    return "\n\n".join([head, *lines]) if lines else f"{head}\n\n{CAB_NO_ADS}"


def cab_stats_line(title: str, st: dict[str, object], budget_left: Decimal) -> str:
    return (
        f"• {post_preview(title, 50)}: размещений {st['placements']}, просмотров {st['views']}, "
        f"кликов {st['clicks']}, потрачено {rub(st['spent'])}, "  # type: ignore[arg-type]
        f"осталось {rub(budget_left)}"
    )
