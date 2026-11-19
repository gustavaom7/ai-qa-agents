"""Human review of findings: merge each run into the committed record.

``findings/<agent>.json`` is the source of truth. A nightly run's findings are
merged into it by fingerprint: a finding seen before keeps its review status
and note (and gets fresh evidence and ``last_seen``); a new one is added as a
draft. Nothing is ever removed automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .finding import Finding, ReviewStatus


@dataclass(frozen=True)
class MergeResult:
    findings: list[Finding]
    new: list[Finding]
    seen_again: list[Finding]


def merge(existing: list[Finding], incoming: list[Finding]) -> MergeResult:
    by_fp = {f.fingerprint: f for f in existing}
    now = datetime.now(UTC)
    new: list[Finding] = []
    seen_again: list[Finding] = []
    for f in incoming:
        old = by_fp.get(f.fingerprint)
        if old is None:
            added = f.model_copy(update={"status": ReviewStatus.DRAFT, "last_seen": now})
            by_fp[f.fingerprint] = added
            new.append(added)
        else:
            updated = old.model_copy(
                update={"evidence": f.evidence, "steps": f.steps, "last_seen": now}
            )
            by_fp[f.fingerprint] = updated
            seen_again.append(updated)
    # Keep the original order, then the new ones.
    order = [f.fingerprint for f in existing] + [f.fingerprint for f in new]
    return MergeResult([by_fp[fp] for fp in order], new, seen_again)


def set_status(
    findings: list[Finding], fingerprint: str, status: ReviewStatus, note: str = ""
) -> Finding:
    """Mark one finding by fingerprint (a unique prefix is enough)."""
    matches = [f for f in findings if f.fingerprint.startswith(fingerprint)]
    if not matches:
        raise KeyError(f"no finding with fingerprint {fingerprint!r}")
    if len(matches) > 1:
        raise KeyError(f"fingerprint prefix {fingerprint!r} is ambiguous")
    target = matches[0]
    index = findings.index(target)
    findings[index] = target.model_copy(update={"status": status, "review_note": note})
    return findings[index]
