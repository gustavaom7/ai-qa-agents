"""Shared helpers for agent tests: a scripted model and a real (local) browser."""

from __future__ import annotations

import functools
import json
import re
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from ai_qa_agents.core.llm import LLMClient, LLMRequest, Provider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class ScriptedProvider(Provider):
    """Plays the model from a script. Each item names the element by label;
    the provider finds its ref in the prompt, the way a real model would."""

    name = "scripted"

    def __init__(self, script: list[dict[str, Any]]) -> None:
        super().__init__(model="script")
        self.script = list(script)
        self.prompts: list[str] = []

    def is_configured(self) -> bool:
        return True

    def _call(self, request: LLMRequest) -> tuple[str, int, int]:
        self.prompts.append(request.prompt)
        if not self.script:
            return json.dumps({"action": {"type": "done"}}), 10, 5
        item = dict(self.script.pop(0))
        if "raw" in item:
            return str(item["raw"]), 10, 5
        label = item.pop("label", None)
        action = {"type": item.pop("type"), "value": item.pop("value", "")}
        if label is not None:
            action["ref"] = ref_for(request.prompt, label)
        return json.dumps({"thought": "scripted", "action": action, **item}), 10, 5


def ref_for(prompt: str, label: str) -> str:
    section = prompt.split("ELEMENTS:", 1)[1].split("IMAGES:", 1)[0]
    for line in section.splitlines():
        if label in line:
            match = re.match(r"(e\d+) ", line)
            if match:
                return match.group(1)
    return "e999"  # not on the page: the agent must cope


def scripted_client(script: list[dict[str, Any]]) -> tuple[LLMClient, ScriptedProvider]:
    provider = ScriptedProvider(script)
    return LLMClient([provider], mode="live"), provider


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def serve(directory: Path, port: int = 0) -> ThreadingHTTPServer:
    """Serve a fixture folder on 127.0.0.1 (port 0 = any free port)."""
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="session")
def bugshop_url() -> Iterator[str]:
    """The bug shop over local HTTP: like a real site (favicon 404s included)."""
    server = serve(FIXTURES / "bugshop")
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()
