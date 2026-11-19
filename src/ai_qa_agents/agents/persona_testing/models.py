"""Data the persona agent works with: personas, missions, decisions and checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator

from ai_qa_agents.core import Severity

DATA_DIR = Path(__file__).parent / "data"

ActionType = Literal["click", "fill", "select", "press", "goto", "done", "give_up"]
CheckKind = Literal[
    "attr_all_equal",
    "not_sorted",
    "input_value_differs",
    "visible",
    "url_lacks",
    "text_lacks",
    "console_error",
    "slow_step",
]
CHECK_KINDS: tuple[str, ...] = CheckKind.__args__  # type: ignore[attr-defined]


class Persona(BaseModel):
    id: str
    name: str
    behaviour: str  # how this user acts, written for the model
    keyboard_only: bool = False
    network: Literal["normal", "slow_3g"] = "normal"
    # Steps slower than this are flagged by the deterministic slow-step detector.
    slow_step_ms: int = 3000


class Mission(BaseModel):
    id: str
    persona: str
    user: str
    goal: str
    max_steps: int = 25


class Check(BaseModel):
    """A deterministic test that is TRUE when the bug is present.

    The model proposes it; only code evaluates it, on the live page and again
    on a fresh replay. A finding counts only if both evaluations are true.
    """

    kind: CheckKind
    selector: str = ""
    attr: str = ""
    expected: str = ""
    by: Literal["text", "number"] = "text"
    order: Literal["asc", "desc"] = "asc"
    step: int = -1  # slow_step: index of the step; -1 = the step the issue was seen at
    ms: int = 3000
    min_count: int = 2

    @field_validator("selector")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class Issue(BaseModel):
    title: str
    severity: Severity = Severity.MEDIUM
    expected: str = ""
    actual: str = ""
    check: Check


class Action(BaseModel):
    type: ActionType
    ref: str = ""  # element ref from the observation, e.g. "e7"
    value: str = ""  # text to type, option to select, key to press or URL


class Decision(BaseModel):
    thought: str = ""
    action: Action
    issue: Issue | None = None


@dataclass
class Step:
    """One executed action, stored with a real selector so it can be replayed."""

    index: int
    type: str
    selector: str = ""
    label: str = ""
    value: str = ""
    duration_ms: int = 0
    error: str = ""
    url_after: str = ""

    def describe(self) -> str:
        target = f" {self.label!r}" if self.label else ""
        where = f" ({self.selector})" if self.selector else ""
        if self.type == "fill":
            return f"Type {self.value!r} into{target}{where}"
        if self.type == "select":
            return f"Select {self.value!r} in{target}{where}"
        if self.type == "press":
            return f"Press {self.value}{where}"
        if self.type == "goto":
            return f"Open {self.value}"
        return f"Click{target}{where}"


@dataclass
class Candidate:
    """A suspected bug, before replay."""

    source: Literal["model", "detector"]
    step_index: int  # number of steps executed when it was seen
    issue: Issue


@dataclass
class MissionRun:
    mission: Mission
    persona: Persona
    steps: list[Step] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    ended: str = ""  # done | give_up | max_steps | error


def load_personas(path: Path = DATA_DIR / "personas.yaml") -> dict[str, Persona]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    personas = [Persona.model_validate(p) for p in raw]
    return {p.id: p for p in personas}


def load_missions(target: str, directory: Path = DATA_DIR / "missions") -> list[Mission]:
    path = directory / f"{target}.yaml"
    if not path.exists():
        return []
    return [Mission.model_validate(m) for m in yaml.safe_load(path.read_text(encoding="utf-8"))]
