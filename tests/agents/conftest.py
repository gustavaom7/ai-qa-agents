"""Shared helpers for agent tests: a scripted model and a real (local) browser."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, sync_playwright

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


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """Real Chromium on local fixture pages (no network). Skips if no browser is installed.

    CI installs Chromium; locally ``PW_CHANNEL=chrome`` uses an installed Chrome.
    """
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(channel=os.getenv("PW_CHANNEL") or None)
        except Exception as exc:  # pragma: no cover - depends on the machine
            pytest.skip(f"no browser available: {str(exc).splitlines()[0]}")
        yield b
        b.close()
