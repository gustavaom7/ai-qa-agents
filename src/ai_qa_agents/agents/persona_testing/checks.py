"""Deterministic checks: code, not the model, decides whether a bug is present.

Every check answers "is the bug there right now?". ``valid`` is False when the
check cannot be evaluated (selector matches nothing, too few items, bad
selector), which is different from "evaluated and the bug is absent".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError

from .driver import Session
from .models import Check, Step

NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class CheckResult:
    valid: bool
    bug: bool
    detail: str

    @property
    def confirmed(self) -> bool:
        return self.valid and self.bug


def _invalid(detail: str) -> CheckResult:
    return CheckResult(valid=False, bug=False, detail=detail)


def _number(text: str) -> float | None:
    match = NUMBER.search(text.replace(",", ""))
    return float(match.group()) if match else None


def evaluate(check: Check, session: Session, steps: list[Step]) -> CheckResult:
    try:
        return _evaluate(check, session, steps)
    except PlaywrightError as exc:
        return _invalid(f"selector error: {str(exc).splitlines()[0][:200]}")


def _evaluate(check: Check, session: Session, steps: list[Step]) -> CheckResult:
    kind = check.kind
    if kind == "url_lacks":
        if not check.expected:
            return _invalid("url_lacks needs 'expected'")
        url = session.url
        return CheckResult(True, check.expected not in url, f"url is {url}")

    if kind == "console_error":
        errors = session.console_errors()
        if check.expected:  # a specific message must appear again
            matching = [e for e in errors if check.expected in e]
            detail = matching[0] if matching else f"no console error matching {check.expected!r}"
            return CheckResult(True, bool(matching), detail[:200])
        return CheckResult(True, bool(errors), "; ".join(errors[:3]) or "no console errors")

    if kind == "slow_step":
        if not steps:
            return _invalid("no steps executed")
        index = check.step if 0 <= check.step < len(steps) else len(steps) - 1
        step = steps[index]
        return CheckResult(
            True,
            step.duration_ms > check.ms,
            f"step {index} ({step.describe()}) took {step.duration_ms} ms, limit {check.ms} ms",
        )

    if not check.selector:
        return _invalid(f"{kind} needs a selector")

    if kind == "attr_all_equal":
        if not check.attr:
            return _invalid("attr_all_equal needs 'attr'")
        values = [v for v in session.attrs(check.selector, check.attr) if v is not None]
        if len(values) < check.min_count:
            return _invalid(f"only {len(values)} element(s) with {check.attr}")
        distinct = sorted(set(values))
        return CheckResult(
            True,
            len(distinct) == 1,
            f"{len(values)} elements, {len(distinct)} distinct {check.attr}: {distinct[:3]}",
        )

    if kind == "not_sorted":
        texts = session.texts(check.selector)
        if check.by == "number":
            numbers = [_number(t) for t in texts]
            keys: list[float] | list[str] = [n for n in numbers if n is not None]
        else:
            keys = [t.lower() for t in texts]
        if len(keys) < 2:
            return _invalid(f"only {len(keys)} sortable item(s)")
        expected = sorted(keys, reverse=check.order == "desc")
        return CheckResult(
            True, list(keys) != expected, f"order {list(keys)[:6]} vs {check.order} sort"
        )

    if kind == "input_value_differs":
        if not check.expected:
            return _invalid("input_value_differs needs 'expected'")
        values = session.input_values(check.selector)
        if not values:
            return _invalid("selector matched no input")
        return CheckResult(True, values[0] != check.expected, f"value is {values[0]!r}")

    if kind == "visible":
        shown = session.visible(check.selector)
        return CheckResult(True, shown, "visible" if shown else "not visible")

    if kind == "text_lacks":
        if not check.expected:
            return _invalid("text_lacks needs 'expected'")
        texts = session.texts(check.selector)
        if not texts:
            return _invalid("selector matched nothing")
        joined = " | ".join(texts)
        return CheckResult(True, check.expected not in joined, f"text is {joined[:120]!r}")

    return _invalid(f"unknown check kind {kind!r}")
