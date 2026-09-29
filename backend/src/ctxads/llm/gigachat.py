"""GigaChat — прод-альтернатива NIM. Токен доступа получаем по OAuth и кэшируем."""

from __future__ import annotations

import time
import uuid

import httpx

from ctxads.llm.base import LLMUnavailable
from ctxads.llm.retry import RetryableLLMError, with_retries

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"


class GigaChatLLM:
    name = "gigachat"

    def __init__(
        self,
        *,
        auth_key: str,
        scope: str = "GIGACHAT_API_PERS",
        model: str = "GigaChat",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        # Сертификаты Сбера выпущены Минцифры: в проде добавьте их CA в trust store.
        self._http = http or httpx.AsyncClient(timeout=60)
        self._auth_key = auth_key
        self._scope = scope
        self._model = model
        self._token: str | None = None
        self._token_exp = 0.0

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        resp = await self._http.post(
            OAUTH_URL,
            data={"scope": self._scope},
            headers={"Authorization": f"Basic {self._auth_key}", "RqUID": str(uuid.uuid4())},
        )
        if resp.status_code >= 400:
            raise LLMUnavailable(f"gigachat oauth {resp.status_code}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_exp = data.get("expires_at", 0) / 1000 or time.time() + 1500
        return self._token

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        async def call() -> str:
            token = await self._access_token()
            try:
                resp = await self._http.post(
                    CHAT_URL,
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as e:
                raise RetryableLLMError(type(e).__name__) from e
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RetryableLLMError(str(resp.status_code))
            if resp.status_code >= 400:
                raise LLMUnavailable(f"gigachat status {resp.status_code}")
            return resp.json()["choices"][0]["message"]["content"]

        return await with_retries(call, name=self.name)
