"""Each provider builds the right request and parses the right answer.

No network: ``httpx.MockTransport`` plays the API.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from ai_qa_agents.core.llm import (
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
    Image,
    LLMError,
    LLMRequest,
    OllamaProvider,
    ProviderUnavailableError,
)

REQ = LLMRequest(prompt="hi", system="be brief", images=(Image(b"png-bytes"),), task="t.x")


def mock(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def capture(status: int, body: dict[str, Any]) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return mock(handler), seen


@pytest.fixture(autouse=True)
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("GROQ_API_KEY", "q-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama.test:11434")


def test_gemini_request_and_parse() -> None:
    http, seen = capture(
        200,
        {
            "candidates": [{"content": {"parts": [{"text": "hel"}, {"text": "lo"}]}}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2},
        },
    )
    resp = GeminiProvider(model="gemini-test", http=http).complete(REQ)
    assert (resp.text, resp.input_tokens, resp.output_tokens) == ("hello", 7, 2)
    assert resp.provider == "gemini"
    sent = seen[0]
    assert sent.url.path.endswith("/models/gemini-test:generateContent")
    assert sent.headers["x-goog-api-key"] == "g-key"
    body = json.loads(sent.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert body["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"


def test_gemini_empty_candidates_is_unavailable() -> None:
    http, _ = capture(200, {"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(ProviderUnavailableError):
        GeminiProvider(http=http).complete(REQ)


def test_groq_request_and_parse() -> None:
    http, seen = capture(
        200,
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        },
    )
    resp = GroqProvider(http=http).complete(REQ)
    assert (resp.text, resp.input_tokens, resp.output_tokens) == ("ok", 5, 1)
    sent = seen[0]
    assert sent.headers["authorization"] == "Bearer q-key"
    body = json.loads(sent.content)
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_ollama_request_and_parse() -> None:
    http, seen = capture(
        200, {"message": {"content": "local"}, "prompt_eval_count": 3, "eval_count": 4}
    )
    resp = OllamaProvider(http=http).complete(REQ)
    assert (resp.text, resp.input_tokens, resp.output_tokens) == ("local", 3, 4)
    assert str(seen[0].url) == "http://ollama.test:11434/api/chat"
    body = json.loads(seen[0].content)
    assert body["stream"] is False
    assert body["messages"][1]["images"] == [REQ.images[0].b64()]


def test_anthropic_request_and_parse() -> None:
    http, seen = capture(
        200,
        {
            "content": [{"type": "text", "text": "claude"}],
            "usage": {"input_tokens": 9, "output_tokens": 3},
        },
    )
    resp = AnthropicProvider(http=http).complete(REQ)
    assert (resp.text, resp.input_tokens, resp.output_tokens) == ("claude", 9, 3)
    sent = seen[0]
    assert sent.headers["x-api-key"] == "a-key"
    assert sent.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(sent.content)
    assert body["system"] == "be brief"
    assert body["messages"][0]["content"][0]["type"] == "image"


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_retryable_status_is_unavailable(status: int) -> None:
    http, _ = capture(status, {"error": "nope"})
    with pytest.raises(ProviderUnavailableError, match=str(status)):
        GroqProvider(http=http).complete(REQ)


def test_bad_request_is_a_real_error() -> None:
    http, _ = capture(400, {"error": "bad field"})
    with pytest.raises(LLMError) as info:
        GroqProvider(http=http).complete(REQ)
    assert not isinstance(info.value, ProviderUnavailableError)


def test_network_error_is_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderUnavailableError, match="network"):
        OllamaProvider(http=mock(boom)).complete(REQ)


def test_missing_key_is_unavailable_without_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY")

    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call the API without a key")

    with pytest.raises(ProviderUnavailableError, match="not configured"):
        GeminiProvider(http=mock(never)).complete(REQ)


def test_ollama_ping_checks_the_server() -> None:
    up, _ = capture(200, {"models": []})
    assert OllamaProvider(http=up).ping() is True

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert OllamaProvider(http=mock(down)).ping() is False
