from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import Channel, ChannelSettings, ChannelStatus


async def get(s: AsyncSession, chat_id: int, *, for_update: bool = False) -> Channel | None:
    return await s.get(Channel, chat_id, with_for_update=for_update, populate_existing=for_update)


async def upsert(
    s: AsyncSession, chat_id: int, owner_user_id: int, *, status: str, title: str | None = None
) -> Channel:
    stmt = insert(Channel).values(
        chat_id=chat_id, owner_user_id=owner_user_id, status=status, title=title
    )
    set_: dict[str, object] = {"owner_user_id": owner_user_id, "status": status}
    if title is not None:
        set_["title"] = title
    await s.execute(stmt.on_conflict_do_update(index_elements=[Channel.chat_id], set_=set_))
    await s.execute(insert(ChannelSettings).values(chat_id=chat_id).on_conflict_do_nothing())
    channel = await s.get(Channel, chat_id, populate_existing=True)
    assert channel is not None
    return channel


async def set_status(s: AsyncSession, chat_id: int, status: str) -> None:
    await s.execute(update(Channel).where(Channel.chat_id == chat_id).values(status=status))


async def get_settings(
    s: AsyncSession, chat_id: int, *, for_update: bool = False
) -> ChannelSettings:
    cs = await s.get(ChannelSettings, chat_id, with_for_update=for_update)
    if cs is None:
        await s.execute(insert(ChannelSettings).values(chat_id=chat_id).on_conflict_do_nothing())
        cs = await s.get(ChannelSettings, chat_id)
    assert cs is not None
    return cs


async def list_by_owner(s: AsyncSession, owner_user_id: int) -> list[Channel]:
    q = (
        select(Channel)
        .where(Channel.owner_user_id == owner_user_id, Channel.status != ChannelStatus.REMOVED)
        .order_by(Channel.created_at)
    )
    return list((await s.scalars(q)).all())


async def list_pending_consent(s: AsyncSession, owner_user_id: int) -> list[Channel]:
    q = select(Channel).where(
        Channel.owner_user_id == owner_user_id,
        Channel.status == ChannelStatus.PENDING_CONSENT,
    )
    return list((await s.scalars(q)).all())


async def toggle_in_list(s: AsyncSession, chat_id: int, field: str, value: str) -> bool:
    """Переключает value в массиве настроек канала. True — значение теперь в списке."""
    cs = await get_settings(s, chat_id)
    current = list(getattr(cs, field) or [])
    if value in current:
        current.remove(value)
        present = False
    else:
        current.append(value)
        present = True
    setattr(cs, field, current)
    return present


async def add_to_list(s: AsyncSession, chat_id: int, field: str, value: str) -> None:
    cs = await get_settings(s, chat_id, for_update=True)
    current = list(getattr(cs, field) or [])
    if value not in current:
        setattr(cs, field, [*current, value])
