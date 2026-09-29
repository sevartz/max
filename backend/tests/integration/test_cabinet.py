from __future__ import annotations

import json
from decimal import Decimal

from ctxads.bot.dispatcher import Dispatcher
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import dialogs as dialogs_repo
from ctxads.max_api.models import Update
from tests.conftest import OWNER_ID, callback, load_update

OTHER_USER = 555


def _say(text: str, user_id: int = OWNER_ID) -> Update:
    raw = load_update("message_created_dialog")
    raw["message"]["body"]["text"] = text
    raw["message"]["sender"]["user_id"] = user_id
    return Update.model_validate(raw)


def _last_text(max_mock, user_id: int = OWNER_ID) -> str:
    return max_mock.sent(user_id=user_id)[-1]["text"]


def _payloads(message: dict) -> list[str]:
    return [b["payload"] for row in message["attachments"][0]["payload"]["buttons"] for b in row]


async def _create_ad(d: Dispatcher, max_mock) -> None:
    await d.dispatch(_say("/cabinet"))
    await d.dispatch(_say("Бегун"))
    await d.dispatch(callback("a:new"))
    await d.dispatch(_say("Кроссовки для бега со скидкой 30%"))
    await d.dispatch(_say("Подберём беговые кроссовки под асфальт и трейл."))
    await d.dispatch(_say("https://example.com/run"))
    await d.dispatch(callback("a:cat:sport"))
    await d.dispatch(callback("a:pm:cpc"))
    await d.dispatch(_say("15"))
    await d.dispatch(_say("3000"))
    await d.dispatch(callback("a:go"))


async def test_create_ad_through_cabinet(ctx, max_mock):
    d = Dispatcher(ctx)
    await d.dispatch(_say("/cabinet"))
    assert "Как называется ваша компания" in _last_text(max_mock)

    await d.dispatch(_say("Бегун"))
    menu = max_mock.sent(user_id=OWNER_ID)[-1]
    assert "Кабинет рекламодателя «Бегун»" in menu["text"]
    assert "a:new" in _payloads(menu)

    await d.dispatch(callback("a:new"))
    await d.dispatch(_say("Кроссовки для бега со скидкой 30%"))
    await d.dispatch(_say("Подберём беговые кроссовки под асфальт и трейл."))
    await d.dispatch(_say("не ссылка"))
    assert "https://example.com" in _last_text(max_mock)  # подсказка при ошибке
    await d.dispatch(_say("https://example.com/run"))
    categories = max_mock.sent(user_id=OWNER_ID)[-1]
    assert "a:cat:sport" in _payloads(categories) and "a:cat:other" not in _payloads(categories)

    await d.dispatch(_say("спорт"))  # на шаге с кнопками текст не принимаем
    assert "кнопкой" in _last_text(max_mock)
    await d.dispatch(callback("a:cat:sport"))
    await d.dispatch(callback("a:pm:cpc"))
    assert "CPC" in _last_text(max_mock)
    await d.dispatch(_say("15"))
    await d.dispatch(_say("10"))  # бюджет меньше цены
    assert "не может быть меньше цены" in _last_text(max_mock)
    await d.dispatch(_say("3000"))
    preview = max_mock.sent(user_id=OWNER_ID)[-1]
    assert "#Реклама. Бегун." in preview["text"] and "a:go" in _payloads(preview)

    await d.dispatch(callback("a:go"))
    await d.dispatch(callback("a:go"))  # повторное нажатие не создаёт дубль

    async with ctx.sessions() as s:
        advertiser = await ads_repo.get_advertiser_by_owner(s, OWNER_ID)
        ads = await ads_repo.list_by_advertiser(s, advertiser.id)
        state = await dialogs_repo.get(s, OWNER_ID)
    assert len(ads) == 1 and state is None
    ad = ads[0]
    assert (ad.category, ad.pricing_model, ad.price, ad.budget_left) == (
        "sport",
        "cpc",
        Decimal("15.00"),
        Decimal("3000.00"),
    )
    assert ad.status == "active" and ad.embedding is not None and ad.erid.startswith("DEMO-")
    card = max_mock.sent(user_id=OWNER_ID)[-1]
    assert "показывается" in card["text"] and f"a:pause:{ad.id}" in _payloads(card)


async def test_new_ad_takes_part_in_matching(ctx, max_mock):
    d = Dispatcher(ctx)
    await _create_ad(d, max_mock)
    async with ctx.sessions() as s:
        [ad] = await ads_repo.list_by_advertiser(
            s, (await ads_repo.get_advertiser_by_owner(s, OWNER_ID)).id
        )
        found = [a.id for a, _ in await ads_repo.search_similar(s, ad.embedding)]
    assert ad.id in found


async def test_pause_topup_edit_and_stats(ctx, max_mock):
    d = Dispatcher(ctx)
    await _create_ad(d, max_mock)
    async with ctx.sessions() as s:
        advertiser = await ads_repo.get_advertiser_by_owner(s, OWNER_ID)
        [ad] = await ads_repo.list_by_advertiser(s, advertiser.id)
    old_embedding = list(ad.embedding)

    await d.dispatch(callback(f"a:pause:{ad.id}"))
    async with ctx.sessions() as s:
        paused = await ads_repo.get(s, ad.id)
        found = [a.id for a, _ in await ads_repo.search_similar(s, old_embedding)]
    assert paused.status == "paused" and ad.id not in found  # на паузе в подбор не попадает
    assert "на паузе" in max_mock.edits()[-1]["text"]
    await d.dispatch(callback(f"a:pause:{ad.id}"))

    await d.dispatch(callback(f"a:top:{ad.id}"))
    await d.dispatch(_say("500"))
    assert any("пополнен на 500 ₽" in m["text"] for m in max_mock.sent(user_id=OWNER_ID))

    await d.dispatch(callback(f"a:ed:{ad.id}:title"))
    await d.dispatch(_say("Спортивные часы с пульсометром"))
    await d.dispatch(callback(f"a:ed:{ad.id}:category"))
    await d.dispatch(callback("a:cat:tech"))

    async with ctx.sessions() as s:
        edited = await ads_repo.get(s, ad.id)
    assert edited.status == "active"
    assert (edited.budget_total, edited.budget_left) == (Decimal("3500.00"), Decimal("3500.00"))
    assert edited.title == "Спортивные часы с пульсометром" and edited.category == "tech"
    assert list(edited.embedding) != old_embedding  # эмбеддинг пересчитан под новый смысл

    await d.dispatch(callback("a:stats"))
    stats = _last_text(max_mock)
    assert "«Бегун»: размещений 0" in stats and "Спортивные часы" in stats


async def test_foreign_user_cannot_touch_ad(ctx, max_mock):
    d = Dispatcher(ctx)
    await _create_ad(d, max_mock)
    async with ctx.sessions() as s:
        advertiser = await ads_repo.get_advertiser_by_owner(s, OWNER_ID)
        [ad] = await ads_repo.list_by_advertiser(s, advertiser.id)

    await d.dispatch(callback(f"a:pause:{ad.id}", user_id=OTHER_USER))
    await d.dispatch(callback(f"a:top:{ad.id}", user_id=OTHER_USER))
    async with ctx.sessions() as s:
        same = await ads_repo.get(s, ad.id)
        state = await dialogs_repo.get(s, OTHER_USER)
    assert same.status == "active" and state is None
    last_answer = json.loads(max_mock.calls("POST", "/answers")[-1].content)
    assert "устарела" in last_answer["notification"]


async def test_cancel_and_commands_reset_input(ctx, max_mock):
    d = Dispatcher(ctx)
    await d.dispatch(_say("/cancel"))
    assert "Нечего отменять" in _last_text(max_mock)

    await d.dispatch(_say("/cabinet"))
    await d.dispatch(_say("/cancel"))
    assert "Ввод отменён" in _last_text(max_mock)
    await d.dispatch(_say("Бегун"))  # ввод прерван — это уже не название компании
    assert "Не понял команду" in _last_text(max_mock)

    await d.dispatch(_say("/cabinet"))
    await d.dispatch(_say("/help"))  # любая команда прерывает ввод
    async with ctx.sessions() as s:
        assert await dialogs_repo.get(s, OWNER_ID) is None
        assert await ads_repo.get_advertiser_by_owner(s, OWNER_ID) is None
