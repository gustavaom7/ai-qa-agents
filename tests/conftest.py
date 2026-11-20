"""Fixtures shared by all tests."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, sync_playwright


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
