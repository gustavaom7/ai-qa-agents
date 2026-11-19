"""The finding schema every agent emits.

One schema for all agents means the report, deduplication and the precision
metric are written once. A finding starts as a ``draft``; only a human moves
it to ``confirmed`` or ``false_positive``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, computed_field


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"


class Evidence(BaseModel):
    kind: str  # screenshot | video | trace | diff | log
    path: str
    note: str = ""


class Finding(BaseModel):
    agent: str
    target: str
    title: str
    severity: Severity
    summary: str
    steps: list[str] = Field(default_factory=list)
    expected: str = ""
    actual: str = ""
    location: str = ""  # page URL, screen name or selector
    evidence: list[Evidence] = Field(default_factory=list)
    reproduced: bool = False  # set by a deterministic replay, never by the LLM
    status: ReviewStatus = ReviewStatus.DRAFT
    review_note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fingerprint(self) -> str:
        """Same agent + target + location + normalized title = same finding."""
        title = re.sub(r"[^a-z0-9]+", " ", self.title.lower()).strip()
        raw = f"{self.agent}|{self.target}|{self.location}|{title}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def dedupe(findings: Iterable[Finding]) -> list[Finding]:
    """Keep the first finding per fingerprint, preserving order."""
    seen: set[str] = set()
    unique: list[Finding] = []
    for f in findings:
        if f.fingerprint not in seen:
            seen.add(f.fingerprint)
            unique.append(f)
    return unique


def precision(findings: Iterable[Finding]) -> float | None:
    """Confirmed / reviewed. None until at least one finding was reviewed."""
    reviewed = [f for f in findings if f.status != ReviewStatus.DRAFT]
    if not reviewed:
        return None
    confirmed = sum(f.status == ReviewStatus.CONFIRMED for f in reviewed)
    return round(confirmed / len(reviewed), 3)


def save_findings(findings: Iterable[Finding], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [f.model_dump(mode="json") for f in findings]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_findings(path: Path) -> list[Finding]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    for item in raw:
        item.pop("fingerprint", None)  # computed, not stored input
    return [Finding.model_validate(item) for item in raw]
