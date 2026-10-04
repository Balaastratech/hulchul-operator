"""Offline audit fixtures: synthetic DOM only, no credentials or network."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope="module")
def browser():
    """Use installed system Chrome; refuse all network requests."""
    with sync_playwright() as pw:
        instance = pw.chromium.launch(channel="chrome", headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    context.route("**/*", lambda route: route.abort())
    tab = context.new_page()
    yield tab
    context.close()


@pytest.fixture
def audit_dir():
    """Keep all scratch artifacts inside this worktree, including local snapshots."""
    import os
    import tempfile

    root = Path(__file__).resolve().parents[2] / "runs" / "audit"
    root.mkdir(parents=True, exist_ok=True)
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(dir=root) as directory:
        try:
            yield Path(directory)
        finally:
            os.chdir(original_cwd)
