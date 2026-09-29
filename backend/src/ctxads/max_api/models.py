"""Модели MAX Bot API. Везде extra="allow": новые поля API не должны ломать парсинг."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class User(_Model):
    user_id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    is_bot: bool = False
    name: str | None = None  # устаревшее поле, встречается в старых ответах

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or self.name or self.username or str(self.user_id)


class Recipient(_Model):
    chat_id: int | None = None
    chat_type: str | None = None  # dialog | chat | channel
    user_id: int | None = None


class MessageBody(_Model):
    mid: str
    seq: int | None = None
    text: str | None = None
    attachments: list[dict[str, Any]] | None = None


class MessageStat(_Model):
    views: int | None = None


class Message(_Model):
    sender: User | None = None
    recipient: Recipient
    timestamp: int = 0
    body: MessageBody | None = None
    stat: MessageStat | None = None
    url: str | None = None
    link: dict[str, Any] | None = None

    @property
    def mid(self) -> str | None:
        return self.body.mid if self.body else None

    @property
    def text(self) -> str:
        return (self.body.text or "") if self.body else ""

    @property
    def is_channel_post(self) -> bool:
        return self.recipient.chat_type == "channel"

    @property
    def is_dialog(self) -> bool:
        return self.recipient.chat_type == "dialog"


class Callback(_Model):
    # TODO(verify-api): структура Callback не раскрыта на dev.max.ru; поля — по GET /updates.
    timestamp: int | None = None
    callback_id: str
    payload: str | None = None
    user: User


class Update(_Model):
    update_type: str
    timestamp: int = 0
    message: Message | None = None
    callback: Callback | None = None
    chat_id: int | None = None
    user: User | None = None
    is_channel: bool | None = None
    payload: str | None = None
    message_id: str | None = None
    user_locale: str | None = None

    def dedupe_key(self) -> str:
        ref = ""
        if self.callback is not None:
            ref = self.callback.callback_id
        elif self.message is not None and self.message.mid:
            ref = self.message.mid
        elif self.message_id:
            ref = self.message_id
        chat = self.chat_id
        if chat is None and self.message is not None:
            chat = self.message.recipient.chat_id
        return f"{self.update_type}:{chat}:{ref}:{self.timestamp}"


class Chat(_Model):
    chat_id: int
    type: str | None = None
    status: str | None = None
    title: str | None = None
    participants_count: int | None = None
    description: str | None = None
    owner_id: int | None = None


class ChatMember(_Model):
    user_id: int
    is_owner: bool = False
    is_admin: bool = False
    permissions: list[str] | None = None


class BotInfo(_Model):
    user_id: int
    first_name: str | None = None
    username: str | None = None
    is_bot: bool = True


class UpdateList(_Model):
    updates: list[Update] = []
    marker: int | None = None
