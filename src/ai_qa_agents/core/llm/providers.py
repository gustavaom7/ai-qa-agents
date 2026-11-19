"""LLM providers, all called over plain HTTP with httpx.

No vendor SDKs: one small adapter per API keeps dependencies light and lets
tests swap the transport for ``httpx.MockTransport``.

Every provider maps "try the next one" situations (missing key, rate limit,
server error, network down, bad credentials) to ``ProviderUnavailableError``.
A malformed request (HTTP 400) is a bug in our code and raises ``LLMError``.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import httpx

from .types import LLMError, LLMRequest, LLMResponse, ProviderUnavailableError

DEFAULT_TIMEOUT_S = 120.0


class Provider(ABC):
    name: str = "base"
    # Free tiers and local models report 0. ``None`` means "priced, but not
    # tracked here": the usage log keeps tokens and leaves cost empty.
    cost_per_mtok: tuple[float, float] | None = (0.0, 0.0)

    def __init__(self, model: str, http: httpx.Client | None = None) -> None:
        self.model = model
        self._http = http or httpx.Client(timeout=DEFAULT_TIMEOUT_S)

    @abstractmethod
    def is_configured(self) -> bool:
        """True when the provider has what it needs (API key, host)."""

    @abstractmethod
    def _call(self, request: LLMRequest) -> tuple[str, int, int]:
        """Return (text, input_tokens, output_tokens)."""

    def ping(self) -> bool:
        """Cheap readiness check without spending tokens. Defaults to the config check."""
        return self.is_configured()

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.is_configured():
            raise ProviderUnavailableError(f"{self.name}: not configured")
        start = time.perf_counter()
        text, tokens_in, tokens_out = self._call(request)
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            latency_s=round(time.perf_counter() - start, 3),
        )

    def _post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        try:
            resp = self._http.post(url, json=json, headers=headers)
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(f"{self.name}: network error: {exc}") from exc
        if resp.status_code in (401, 403, 404, 429) or resp.status_code >= 500:
            raise ProviderUnavailableError(
                f"{self.name}: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            raise LLMError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:500]}")
        data: dict[str, Any] = resp.json()
        return data


class GeminiProvider(Provider):
    """Google Gemini API (free tier in AI Studio, no card needed)."""

    name = "gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, model: str | None = None, http: httpx.Client | None = None) -> None:
        super().__init__(model or os.environ.get("GEMINI_MODEL") or "gemini-flash-latest", http)
        self.api_key = os.getenv("GEMINI_API_KEY", "")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _call(self, request: LLMRequest) -> tuple[str, int, int]:
        parts: list[dict[str, Any]] = [{"text": request.prompt}]
        parts += [
            {"inline_data": {"mime_type": img.mime_type, "data": img.b64()}}
            for img in request.images
        ]
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if request.system:
            body["systemInstruction"] = {"parts": [{"text": request.system}]}
        data = self._post(
            f"{self.base_url}/models/{self.model}:generateContent",
            json=body,
            headers={"x-goog-api-key": self.api_key},
        )
        try:
            candidate_parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError) as exc:
            raise ProviderUnavailableError(f"gemini: empty answer: {str(data)[:200]}") from exc
        text = "".join(p.get("text", "") for p in candidate_parts)
        usage = data.get("usageMetadata", {})
        return text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)


class GroqProvider(Provider):
    """Groq, OpenAI-compatible chat completions (free tier, rate limited)."""

    name = "groq"
    base_url = "https://api.groq.com/openai/v1"

    def __init__(self, model: str | None = None, http: httpx.Client | None = None) -> None:
        super().__init__(model or os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile", http)
        self.api_key = os.getenv("GROQ_API_KEY", "")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _call(self, request: LLMRequest) -> tuple[str, int, int]:
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        content += [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{img.mime_type};base64,{img.b64()}"},
            }
            for img in request.images
        ]
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": content})
        data = self._post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


class OllamaProvider(Provider):
    """Local models through Ollama. Free and unlimited, but slow on CI runners."""

    name = "ollama"

    def __init__(self, model: str | None = None, http: httpx.Client | None = None) -> None:
        super().__init__(model or os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b", http)
        self.host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.host)

    def ping(self) -> bool:
        """Ollama needs a running server, so check it answers."""
        try:
            return self._http.get(f"{self.host}/api/tags", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    def _call(self, request: LLMRequest) -> tuple[str, int, int]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        user: dict[str, Any] = {"role": "user", "content": request.prompt}
        if request.images:
            user["images"] = [img.b64() for img in request.images]
        messages.append(user)
        data = self._post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": request.temperature, "num_predict": request.max_tokens},
            },
            headers={},
        )
        text = data.get("message", {}).get("content", "")
        return text, data.get("prompt_eval_count", 0), data.get("eval_count", 0)


class AnthropicProvider(Provider):
    """Claude API. Optional and off by default: only for quality comparisons."""

    name = "anthropic"
    base_url = "https://api.anthropic.com/v1"
    cost_per_mtok = None

    def __init__(self, model: str | None = None, http: httpx.Client | None = None) -> None:
        super().__init__(model or os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-5-5", http)
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _call(self, request: LLMRequest) -> tuple[str, int, int]:
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": img.mime_type, "data": img.b64()},
            }
            for img in request.images
        ]
        content.append({"type": "text", "text": request.prompt})
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": [{"role": "user", "content": content}],
        }
        if request.system:
            body["system"] = request.system
        data = self._post(
            f"{self.base_url}/messages",
            json=body,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        usage = data.get("usage", {})
        return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)


PROVIDERS: dict[str, Callable[[], Provider]] = {
    p.name: p for p in (GeminiProvider, GroqProvider, OllamaProvider, AnthropicProvider)
}
