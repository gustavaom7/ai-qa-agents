"""Apps the agents run against. Public demo apps only."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class TargetKind(StrEnum):
    WEB = "web"
    ANDROID = "android"


class KnownBug(BaseModel):
    """A bug the target is known to have: the answer key that measures recall."""

    id: str
    description: str
    trigger: str = ""  # e.g. "login as problem_user"
    # How an agent's finding is matched to this bug when measuring recall:
    # same test user and same kind of deterministic check ("signal").
    user: str = ""
    signal: str = ""


class Target(BaseModel):
    name: str
    kind: TargetKind
    base_url: str = ""
    app_id: str = ""
    description: str = ""
    credentials: dict[str, str] = Field(default_factory=dict)
    known_bugs: list[KnownBug] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_locator(self) -> Target:
        if self.kind == TargetKind.WEB and not self.base_url:
            raise ValueError(f"web target {self.name!r} needs base_url")
        if self.kind == TargetKind.ANDROID and not self.app_id:
            raise ValueError(f"android target {self.name!r} needs app_id")
        return self


def load_targets(directory: Path) -> dict[str, Target]:
    targets: dict[str, Target] = {}
    for path in sorted(directory.glob("*.yaml")):
        target = Target.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if target.name != path.stem:
            raise ValueError(f"{path.name}: name {target.name!r} must match the file name")
        targets[target.name] = target
    return targets
