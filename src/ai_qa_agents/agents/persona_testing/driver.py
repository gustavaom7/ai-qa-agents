"""Browser session for one mission: observe the page, act, answer checks.

``Session`` is the interface the agent, the checks and the replay use, so unit
tests can swap in a fake. ``PlaywrightSession`` is the real one.
"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from playwright.sync_api import Browser, BrowserContext, Page, Playwright
from playwright.sync_api import Error as PlaywrightError

from .models import Persona, Step

OBSERVE_JS = (Path(__file__).parent / "observe.js").read_text(encoding="utf-8")
MAX_ELEMENTS = 60
ACTION_TIMEOUT_MS = 10_000
SETTLE_TIMEOUT_MS = 15_000

# Roughly Chrome DevTools' "Slow 3G" preset.
SLOW_3G = {
    "offline": False,
    "latency": 400,
    "downloadThroughput": 400 * 1024 // 8,
    "uploadThroughput": 400 * 1024 // 8,
}


@dataclass
class Element:
    ref: str
    tag: str
    type: str
    label: str
    selector: str
    classes: str = ""
    value: str = ""
    options: list[str] = field(default_factory=list)
    disabled: bool = False


@dataclass
class Observation:
    url: str
    title: str
    text: str
    elements: list[Element]
    images: list[dict[str, str]]
    truncated: bool = False

    def element(self, ref: str) -> Element | None:
        return next((e for e in self.elements if e.ref == ref), None)


class Session(Protocol):
    @property
    def url(self) -> str: ...
    def observe(self) -> Observation: ...
    def execute(self, step: Step) -> Step: ...
    def texts(self, selector: str) -> list[str]: ...
    def attrs(self, selector: str, attr: str) -> list[str | None]: ...
    def input_values(self, selector: str) -> list[str]: ...
    def visible(self, selector: str) -> bool: ...
    def console_errors(self) -> list[str]: ...
    def screenshot(self, path: Path) -> None: ...


def launch(pw: Playwright) -> Browser:
    """Chromium from ``playwright install``, or a local Chrome via PW_CHANNEL=chrome."""
    channel = os.getenv("PW_CHANNEL") or None
    headless = os.getenv("PW_HEADED", "") == ""
    return pw.chromium.launch(channel=channel, headless=headless)


class PlaywrightSession:
    def __init__(
        self,
        browser: Browser,
        persona: Persona,
        record_dir: Path | None = None,
        trace: bool = False,
    ) -> None:
        self.persona = persona
        self.record_dir = record_dir
        self._trace = trace
        options: dict[str, Any] = {"viewport": {"width": 1280, "height": 800}}
        if record_dir is not None:
            record_dir.mkdir(parents=True, exist_ok=True)
            options["record_video_dir"] = str(record_dir)
            options["record_video_size"] = {"width": 1280, "height": 800}
        self.context, self.page = self._open(browser, options)
        if trace:
            self.context.tracing.start(screenshots=True, snapshots=True)
        self._errors: list[str] = []
        self.page.on(
            "console", lambda m: self._errors.append(m.text) if m.type == "error" else None
        )
        self.page.on("pageerror", lambda e: self._errors.append(f"pageerror: {e}"))
        if persona.network == "slow_3g":
            cdp = self.context.new_cdp_session(self.page)
            cdp.send("Network.enable")
            cdp.send("Network.emulateNetworkConditions", SLOW_3G)

    @staticmethod
    def _open(browser: Browser, options: dict[str, Any]) -> tuple[BrowserContext, Page]:
        context = browser.new_context(**options)
        try:
            return context, context.new_page()
        except PlaywrightError as exc:
            if "ffmpeg" not in str(exc) or "record_video_dir" not in options:
                raise
            # No ffmpeg (``playwright install ffmpeg`` missing): keep going without video.
            context.close()
            options = {k: v for k, v in options.items() if not k.startswith("record_video")}
            context = browser.new_context(**options)
            return context, context.new_page()

    # -- observing ---------------------------------------------------------

    @property
    def url(self) -> str:
        return self.page.url

    def observe(self) -> Observation:
        raw: dict[str, Any] = self.page.evaluate(OBSERVE_JS, MAX_ELEMENTS)
        elements = [Element(ref=f"e{i + 1}", **el) for i, el in enumerate(raw["elements"])]
        return Observation(
            url=raw["url"],
            title=raw["title"],
            text=raw["text"],
            elements=elements,
            images=raw["images"],
            truncated=raw["truncated"],
        )

    # -- acting ------------------------------------------------------------

    def execute(self, step: Step) -> Step:
        """Run a step and fill in its duration, error and resulting URL."""
        start = time.perf_counter()
        try:
            self._do(step)
            self._settle()
        except PlaywrightError as exc:
            step.error = str(exc).splitlines()[0][:300]
        step.duration_ms = int((time.perf_counter() - start) * 1000)
        step.url_after = self.page.url
        return step

    def _do(self, step: Step) -> None:
        page = self.page
        if step.type == "goto":
            page.goto(step.value, timeout=SETTLE_TIMEOUT_MS * 2)
            return
        if step.type == "press" and not step.selector:
            page.keyboard.press(step.value)
            return
        loc = page.locator(step.selector).first
        if step.type == "click":
            if self.persona.keyboard_only:
                loc.focus(timeout=ACTION_TIMEOUT_MS)
                page.keyboard.press("Enter")
            else:
                loc.click(timeout=ACTION_TIMEOUT_MS)
        elif step.type == "fill":
            loc.fill(step.value, timeout=ACTION_TIMEOUT_MS)
        elif step.type == "select":
            try:
                loc.select_option(label=step.value, timeout=ACTION_TIMEOUT_MS)
            except PlaywrightError:
                loc.select_option(value=step.value, timeout=ACTION_TIMEOUT_MS)
        elif step.type == "press":
            loc.press(step.value, timeout=ACTION_TIMEOUT_MS)
        else:
            raise ValueError(f"cannot execute step type {step.type!r}")

    def _settle(self) -> None:
        # A page that never goes idle is not an action failure.
        with contextlib.suppress(PlaywrightError):
            self.page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)

    # -- answering checks --------------------------------------------------

    def texts(self, selector: str) -> list[str]:
        return [t.strip() for t in self.page.locator(selector).all_inner_texts()]

    def attrs(self, selector: str, attr: str) -> list[str | None]:
        values: list[str | None] = self.page.locator(selector).evaluate_all(
            "(els, a) => els.map(e => e.getAttribute(a))", attr
        )
        return values

    def input_values(self, selector: str) -> list[str]:
        values: list[str] = self.page.locator(selector).evaluate_all(
            "els => els.map(e => String(e.value ?? ''))"
        )
        return values

    def visible(self, selector: str) -> bool:
        loc = self.page.locator(selector)
        return loc.count() > 0 and loc.first.is_visible()

    def console_errors(self) -> list[str]:
        return list(self._errors)

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)

    # -- closing -----------------------------------------------------------

    def close(self, trace_path: Path | None = None) -> Path | None:
        """Close and return the video path, if one was recorded."""
        if self._trace and trace_path is not None:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            self.context.tracing.stop(path=str(trace_path))
        video = self.page.video
        self.context.close()
        if video is None:
            return None
        return Path(video.path())
