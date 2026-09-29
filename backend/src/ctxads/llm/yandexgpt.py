"""YandexGPT (Foundation Models API) — прод-альтернатива NIM, данные остаются в РФ."""

from __future__ import annotations

import httpx

from ctxads.llm.base import LLMUnavailable
from ctxads.llm.retry import RetryableLLMError, with_retries

URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


class YandexGPTLLM:
    name = "yandexgpt"

    def __init__(
        self,
        *,
        api_key: str,
        folder_id: str,
        model: str = "yandexgpt-lite/latest",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._http = http or httpx.AsyncClient(timeout=60)
        self._headers = {"Authorization": f"Api-Key {api_key}", "x-folder-id": folder_id}
        self._model_uri = f"gpt://{folder_id}/{model}"

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        body = {
            "modelUri": self._model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": str(max_tokens),
            },
            "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}],
        }

        async def call() -> str:
            try:
                resp = await self._http.post(URL, json=body, headers=self._headers)
            except httpx.HTTPError as e:
                raise RetryableLLMError(type(e).__name__) from e
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RetryableLLMError(str(resp.status_code))
            if resp.status_code >= 400:
                raise LLMUnavailable(f"yandexgpt status {resp.status_code}")
            return resp.json()["result"]["alternatives"][0]["message"]["text"]

        return await with_retries(call, name=self.name)
