from __future__ import annotations

import asyncio
import logging
import ssl
from pathlib import Path
from typing import Any

import certifi
import httpx

from ctxads.max_api.errors import MaxApiError, MaxNetworkError
from ctxads.max_api.models import BotInfo, Chat, ChatMember, Message, UpdateList
from ctxads.ratelimit import KeyedRateLimiter, RateLimiter

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3

# Сертификат platform-api2.max.ru выпущен УЦ Минцифры (Russian Trusted Root CA), которого нет
# в certifi. Доверяем ему только в этом клиенте: системное хранилище и запросы к LLM не трогаем.
MAX_ROOT_CA = Path(__file__).with_name("russian_trusted_root_ca.pem")


def max_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=MAX_ROOT_CA)
    return ctx


class MaxClient:
    """Тонкий async-клиент MAX Bot API: ретраи на 429/5xx и клиентский rate limit."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        rps: float = 30,
        chat_rps: float = 2,
        http: httpx.AsyncClient | None = None,
        backoff_base: float = 0.5,
    ) -> None:
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=100.0), verify=max_ssl_context()
        )
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": token}
        self._global = RateLimiter(rps)
        self._per_chat = KeyedRateLimiter(chat_rps)
        self._backoff_base = backoff_base

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        chat_key: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._global.acquire()
            if chat_key:
                await self._per_chat.acquire(chat_key)
            try:
                resp = await self._http.request(
                    method,
                    f"{self._base}{path}",
                    params=params,
                    json=json,
                    headers=self._headers,
                    timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except httpx.HTTPError as e:
                err: MaxApiError = MaxNetworkError(type(e).__name__)
            else:
                if resp.status_code < 400:
                    return resp.json() if resp.content else {}
                err = _error_from(resp)
            if not err.retryable or attempt == MAX_ATTEMPTS:
                raise err
            delay = self._backoff_base * 2 ** (attempt - 1)
            log.warning("MAX %s %s failed (%s), retry in %.1fs", method, path, err.status, delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    # --- методы API ---

    async def get_me(self) -> BotInfo:
        return BotInfo.model_validate(await self._request("GET", "/me"))

    async def send_message(
        self,
        *,
        chat_id: int | None = None,
        user_id: int | None = None,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        format: str | None = None,
        notify: bool | None = None,
        disable_link_preview: bool | None = None,
    ) -> Message | None:
        if (chat_id is None) == (user_id is None):
            raise ValueError("нужен ровно один из chat_id / user_id")
        body: dict[str, Any] = {"text": text[:4000]}
        if attachments is not None:
            body["attachments"] = attachments
        if format:
            body["format"] = format
        if notify is not None:
            body["notify"] = notify
        data = await self._request(
            "POST",
            "/messages",
            params={
                "chat_id": chat_id,
                "user_id": user_id,
                "disable_link_preview": _bool(disable_link_preview),
            },
            json=body,
            chat_key=f"c{chat_id}" if chat_id is not None else f"u{user_id}",
        )
        # Документация говорит «возвращает Message»; на практике бывает обёртка {"message": ...}.
        raw = data.get("message", data) if isinstance(data, dict) else None
        return Message.model_validate(raw) if isinstance(raw, dict) and "recipient" in raw else None

    async def edit_message(
        self,
        mid: str,
        *,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        format: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"text": text[:4000], "attachments": attachments or []}
        if format:
            body["format"] = format
        await self._request("PUT", "/messages", params={"message_id": mid}, json=body)

    async def delete_message(self, mid: str) -> None:
        # TODO(verify-api): параметры DELETE /messages на dev.max.ru не расписаны;
        # message_id — по аналогии с PUT /messages.
        await self._request("DELETE", "/messages", params={"message_id": mid})

    async def answer_callback(
        self,
        callback_id: str,
        *,
        notification: str | None = None,
        message: dict[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {}
        if message is not None:
            body["message"] = message
        if notification:
            # TODO(verify-api): поле notification упомянуто в описании ответа, но не в схеме тела.
            body["notification"] = notification
        await self._request("POST", "/answers", params={"callback_id": callback_id}, json=body)

    async def get_updates(
        self, *, marker: int | None = None, timeout: int = 30, limit: int = 100
    ) -> UpdateList:
        data = await self._request(
            "GET",
            "/updates",
            params={"marker": marker, "timeout": timeout, "limit": limit},
            timeout=timeout + 10,
        )
        return UpdateList.model_validate(data)

    async def get_chat(self, chat_id: int) -> Chat:
        return Chat.model_validate(await self._request("GET", f"/chats/{chat_id}"))

    async def get_my_membership(self, chat_id: int) -> ChatMember:
        return ChatMember.model_validate(await self._request("GET", f"/chats/{chat_id}/members/me"))

    async def get_messages(
        self,
        *,
        chat_id: int | None = None,
        message_ids: list[str] | None = None,
        count: int | None = None,
    ) -> list[Message]:
        params: dict[str, Any] = {"chat_id": chat_id, "count": count}
        if message_ids:
            # TODO(verify-api): формат массива message_ids (через запятую vs повтор параметра).
            params["message_ids"] = ",".join(message_ids)
        data = await self._request("GET", "/messages", params=params)
        return [Message.model_validate(m) for m in data.get("messages", [])]

    async def subscribe(self, url: str, update_types: list[str], secret: str | None) -> None:
        body: dict[str, Any] = {"url": url, "update_types": update_types}
        if secret:
            body["secret"] = secret
        await self._request("POST", "/subscriptions", json=body)

    async def unsubscribe(self, url: str) -> None:
        await self._request("DELETE", "/subscriptions", params={"url": url})


def _bool(v: bool | None) -> str | None:
    return None if v is None else str(v).lower()


def _error_from(resp: httpx.Response) -> MaxApiError:
    code = message = None
    try:
        data = resp.json()
        code, message = data.get("code"), data.get("message")
    except ValueError:
        message = resp.text[:200]
    return MaxApiError(resp.status_code, code, message)
