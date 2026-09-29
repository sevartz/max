from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import DialogState


async def get(s: AsyncSession, user_id: int, *, for_update: bool = False) -> DialogState | None:
    return await s.get(DialogState, user_id, with_for_update=for_update, populate_existing=True)


async def set_state(
    s: AsyncSession, user_id: int, flow: str, step: str, data: dict[str, Any]
) -> None:
    values = {"user_id": user_id, "flow": flow, "step": step, "data": data}
    stmt = insert(DialogState).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DialogState.user_id],
        set_={"flow": flow, "step": step, "data": data, "updated_at": func.now()},
    )
    await s.execute(stmt)


async def clear(s: AsyncSession, user_id: int) -> None:
    await s.execute(delete(DialogState).where(DialogState.user_id == user_id))
