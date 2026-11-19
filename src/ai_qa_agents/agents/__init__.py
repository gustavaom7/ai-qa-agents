"""Agent registry.

Each agent module registers a ``run(target, client, out_dir) -> list[Finding]``
function here. Agents register on import, at the bottom of this module.
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


# Import agents last so they can use ``register`` above.
from ai_qa_agents.agents import persona_testing  # noqa: E402, F401
