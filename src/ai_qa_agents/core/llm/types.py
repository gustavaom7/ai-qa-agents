"""Request/response types shared by every LLM provider."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field


class LLMError(Exception):
    """Base error for the LLM layer."""


class ProviderUnavailableError(LLMError):
    """The provider cannot serve this request (no key, rate limited, down).

    The client catches this and moves on to the next provider in the chain.
    """


class AllProvidersFailedError(LLMError):
    """Every provider in the chain was skipped or failed."""


class CassetteMissError(LLMError):
    """Replay mode found no recorded answer for this request."""


@dataclass(frozen=True)
class Image:
    """An image sent to a vision-capable model."""

    data: bytes
    mime_type: str = "image/png"

    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class LLMRequest:
    prompt: str
    system: str | None = None
    images: tuple[Image, ...] = ()
    temperature: float = 0.0
    max_tokens: int = 1024
    # Free-form label used in usage logs, e.g. "persona_testing.next_step".
    task: str = "unspecified"

    def cache_key(self) -> str:
        """Stable key for cassettes.

        Provider and model are deliberately left out: a recorded answer must
        replay no matter which provider of the chain produced it.
        """
        payload = {
            "prompt": self.prompt,
            "system": self.system,
            "images": [img.sha256() for img in self.images],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    from_cassette: bool = False
    extra: dict[str, str] = field(default_factory=dict)
