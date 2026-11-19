"""What the model sees and how its answer is parsed.

The model picks the next action and may report one issue per turn. An issue
must carry a deterministic ``check``; the agent throws it away if code cannot
confirm it on the live page.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from ai_qa_agents.core.llm import LLMClient, LLMRequest

from .driver import Observation
from .models import Decision, Mission, Persona, Step

HISTORY_STEPS = 8

SYSTEM = """\
You are an experienced QA tester exploring a web app while playing a persona.
Pursue the goal the way the persona would, and watch for bugs: wrong data,
broken sorting, fields that lose input, missing or wrong images, errors,
dead ends, very slow steps.

Reply with ONE JSON object and nothing else:
{
  "thought": "<one short sentence>",
  "action": {"type": "click|fill|select|press|goto|done|give_up",
             "ref": "<element ref like e7, for click/fill/select/press>",
             "value": "<text for fill, option label for select, key for press, URL for goto>"},
  "issue": null | {
    "title": "<short bug title>",
    "severity": "critical|high|medium|low",
    "expected": "<what should happen>",
    "actual": "<what you see>",
    "check": {"kind": "...", ...}
  }
}

Report an issue only with concrete evidence on the CURRENT page. Never report
the same issue twice. Each issue needs a check that is TRUE while the bug
exists; code evaluates it, so use selectors from the observation (you may
generalize a repeated element to a shared class, e.g. "img.item_img"):
- attr_all_equal: selector, attr       -> every matched element has the same attr value
- not_sorted: selector, by (text|number), order (asc|desc) -> matched texts are out of order
- input_value_differs: selector, expected -> the input's value is not what was typed
- visible: selector                    -> an error/unexpected element is visible
- url_lacks: expected                  -> the URL does not contain the expected part
- text_lacks: selector, expected       -> the element text does not contain the expected text
- console_error                        -> the page logged errors
- slow_step: ms                        -> the last step took longer than ms

Use "done" when the goal is reached and "give_up" when it is blocked.
"""


def _speed(ms: int) -> str:
    """Coarse buckets, so prompts (and cassette keys) stay stable between runs."""
    if ms < 1000:
        return "fast"
    if ms < 3000:
        return "1-3 s"
    return "slow, over 3 s"


def _history(steps: list[Step], notes: list[str]) -> str:
    lines = []
    for s in steps[-HISTORY_STEPS:]:
        result = f"ERROR: {s.error}" if s.error else "ok"
        lines.append(f"{s.index}. {s.describe()} -> {result}, {_speed(s.duration_ms)}")
    lines += [f"note: {n}" for n in notes[-4:]]
    return "\n".join(lines) or "(nothing yet)"


def _elements(obs: Observation) -> str:
    lines = []
    for e in obs.elements:
        bits = [e.ref, e.tag + (f"[{e.type}]" if e.type else ""), repr(e.label)]
        if e.value:
            bits.append(f"value={e.value!r}")
        if e.options:
            bits.append(f"options={e.options}")
        if e.disabled:
            bits.append("disabled")
        if e.classes:
            bits.append(f".{e.classes}")
        bits.append(e.selector)
        lines.append(" ".join(bits))
    if obs.truncated:
        lines.append("(more elements not shown)")
    return "\n".join(lines) or "(none)"


def _images(obs: Observation) -> str:
    return (
        "\n".join(
            f"{i['selector']} .{i['classes']} alt={i['alt']!r} src=...{i['src']}"
            for i in obs.images
        )
        or "(none)"
    )


def build_prompt(
    mission: Mission,
    persona: Persona,
    credentials: str,
    obs: Observation,
    steps: list[Step],
    notes: list[str],
    reported: list[str],
) -> str:
    return f"""\
PERSONA: {persona.name}. {persona.behaviour}
GOAL: {mission.goal}
LOGIN: username "{mission.user}", password "{credentials}"

STEPS SO FAR:
{_history(steps, notes)}

ISSUES ALREADY REPORTED: {"; ".join(reported) or "none"}

CURRENT PAGE: {obs.title} — {obs.url}
ELEMENTS:
{_elements(obs)}
IMAGES:
{_images(obs)}
PAGE TEXT:
{obs.text}
"""


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_decision(text: str) -> Decision:
    """Parse the model's JSON, tolerating code fences and surrounding prose."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    data = json.loads(cleaned[start : end + 1])
    if isinstance(data.get("issue"), dict) and not data["issue"].get("check"):
        data["issue"] = None  # an issue without a check cannot be confirmed: drop it
    return Decision.model_validate(data)


def decide(
    client: LLMClient,
    mission: Mission,
    persona: Persona,
    credentials: str,
    obs: Observation,
    steps: list[Step],
    notes: list[str],
    reported: list[str],
) -> Decision:
    prompt = build_prompt(mission, persona, credentials, obs, steps, notes, reported)
    request = LLMRequest(
        prompt=prompt, system=SYSTEM, max_tokens=600, task="persona_testing.decide"
    )
    answer = client.complete(request).text
    try:
        return parse_decision(answer)
    except (ValueError, ValidationError) as exc:
        retry = LLMRequest(
            prompt=f"{prompt}\nYOUR LAST ANSWER WAS INVALID ({str(exc)[:200]}). "
            "Reply with the JSON object only.",
            system=SYSTEM,
            max_tokens=600,
            task="persona_testing.decide_retry",
        )
        try:
            return parse_decision(client.complete(retry).text)
        except (ValueError, ValidationError):
            return Decision.model_validate(
                {"thought": "model answer unusable", "action": {"type": "give_up"}}
            )
