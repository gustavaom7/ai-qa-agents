"""Agent registry.

Each agent module registers a ``run(target, client, out_dir) -> list[Finding]``
function here. Delivery 1 ships the core only, so the registry is empty.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ai_qa_agents.core import Finding, Target
from ai_qa_agents.core.llm import LLMClient

AgentFn = Callable[[Target, LLMClient, Path], list[Finding]]

REGISTRY: dict[str, AgentFn] = {}


def register(name: str) -> Callable[[AgentFn], AgentFn]:
    def wrap(fn: AgentFn) -> AgentFn:
        if name in REGISTRY:
            raise ValueError(f"agent {name!r} registered twice")
        REGISTRY[name] = fn
        return fn

    return wrap
