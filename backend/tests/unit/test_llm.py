from __future__ import annotations

import json

import httpx
import openai
import pytest

from ctxads.llm.base import FallbackLLM, LLMUnavailable, extract_json
from ctxads.llm.fake import FakeLLM
from ctxads.llm.openai_compat import OpenAICompatLLM
from ctxads.matching.analyzer import AnalysisFailed, analyze_post


def test_extract_json():
    assert extract_json('```json\n{"a": 1, "b": "}"}\n```') == {"a": 1, "b": "}"}
    assert extract_json('Ответ: {bad} потом {"ok": true}') == {"ok": True}
    assert extract_json("нет json") is None


async def test_analyzer_normalizes_fields():
    llm = FakeLLM(
        [
            '{"summary": "s", "topics": [" Сахар ", ""], "category": "HEALTH", '
            '"intent": "что-то", "brand_safety": "safe", "unsafe_reason": null}'
        ]
    )
    a = await analyze_post(llm, "текст")
    assert a.category == "health" and a.topics == ["сахар"] and a.intent == "информационный"
    assert llm.calls[0]["json_mode"] is True


async def test_analyzer_unknown_category_to_other():
    llm = FakeLLM(['{"summary": "s", "category": "crypto", "brand_safety": "safe"}'])
    assert (await analyze_post(llm, "x")).category == "other"


async def test_analyzer_gives_up_after_one_retry():
    llm = FakeLLM(["мусор", '{"brand_safety": "maybe"}'])
    with pytest.raises(AnalysisFailed):
        await analyze_post(llm, "x")
    assert len(llm.calls) == 2


async def test_fake_llm_flags_tragedy():
    llm = FakeLLM()
    a = await analyze_post(llm, "Трагедия: при крушении поезда погибли люди.")
    assert a.brand_safety == "unsafe"


class _Down:
    name = "down"

    async def chat(self, *a, **kw):
        raise LLMUnavailable("x")


async def test_fallback_provider():
    backup = FakeLLM(["ok"])
    llm = FallbackLLM(_Down(), backup)
    assert await llm.chat("s", "u", temperature=0, max_tokens=5) == "ok"


NIM = "https://nim.test/v1"


def _completion(content: str) -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


class _Server:
    """Мок NIM: отдаёт заранее заданные ответы и запоминает запросы."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def llm(self) -> OpenAICompatLLM:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self))
        client = openai.AsyncOpenAI(
            base_url=NIM, api_key="nvapi-test", max_retries=0, http_client=http
        )
        return OpenAICompatLLM(
            base_url=NIM, api_key="k", model="qwen", client=client, backoff_base=0
        )


async def test_nim_retries_429_and_strips_thinking():
    server = _Server(
        httpx.Response(429, json={}),
        httpx.Response(200, json=_completion('<think>hmm</think>{"a":1}')),
    )
    out = await server.llm().chat("s", "u", temperature=0.2, max_tokens=10, json_mode=True)
    assert out == '{"a":1}' and len(server.requests) == 2
    body = json.loads(server.requests[-1].content)
    assert server.requests[-1].url.path == "/v1/chat/completions"
    assert body["response_format"] == {"type": "json_object"}
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    assert body["max_tokens"] == 10 and body["model"] == "qwen"


async def test_nim_gives_up_after_3_attempts():
    server = _Server(httpx.Response(500, json={}))
    with pytest.raises(LLMUnavailable):
        await server.llm().chat("s", "u", temperature=0.2, max_tokens=10)
    assert len(server.requests) == 3


async def test_nim_auth_error_not_retried():
    server = _Server(httpx.Response(401, json={}))
    with pytest.raises(LLMUnavailable):
        await server.llm().chat("s", "u", temperature=0.2, max_tokens=10)
    assert len(server.requests) == 1
