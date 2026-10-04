"""Real browser attempts an off-allowlist redirect while filling the local ATS."""

import asyncio
import os

import pytest

from src.operator.browser.cdp import CDPBrowserManager
from src.operator.graph.adapters import BrowserBridge
from src.operator.policy.allowlist import DomainAllowlist
from tests.chaos.harness import ChaosScenario


class RedirectEscape(AssertionError):
    """Only observed forbidden destination receipt qualifies for this finding."""


@pytest.mark.skipif(
    os.getenv("RUN_CHAOS") != "1", reason="opt-in fixture browser; owns port 8780"
)
def test_off_allowlist_redirect_during_fill(tmp_path, monkeypatch):
    """Route a synthetic input event into a redirect; forbidden target gets no request."""
    with ChaosScenario(tmp_path) as scenario:
        scenario.reach_review()
        allowed = DomainAllowlist.from_urls(["http://127.0.0.1:8780"])
        bridge = BrowserBridge(CDPBrowserManager(), allowed, tmp_path / "evidence")
        forbidden = []
        received = []
        handler = scenario.fixture.RequestHandlerClass
        original_get = handler.do_GET

        def receive(request):
            if request.headers.get("Host") == "localhost:8780":
                received.append(request.path)
            original_get(request)

        monkeypatch.setattr(handler, "do_GET", receive)

        async def probe():
            await bridge.attach(scenario.cdp, scenario.job["browser_target_id"])
            route_guard = bridge._route

            def guard(route):
                if route.request.url.startswith("http://localhost:8780/"):
                    forbidden.append(route.request.url)
                route_guard(route)

            await bridge._call(bridge.page.unroute, "**/*")
            await bridge._call(bridge.page.route, "**/*", guard)

            def fill_redirect():
                bridge.page.route(
                    "**/redirect-during-fill",
                    lambda route: route.fulfill(
                        status=302, headers={"Location": "http://localhost:8780/ats_a/"}
                    ),
                )
                bridge.page.evaluate("""() => {const el=document.querySelector('[name=email]');
                    el.addEventListener('input', () => location.href='/redirect-during-fill', {once:true});}""")
                bridge.page.locator("[name=email]").fill(
                    "redirect-synthetic@example.test"
                )
                bridge.page.wait_for_timeout(700)

            await bridge._call(fill_redirect)
            await bridge.disconnect()

        try:
            asyncio.run(probe())
        finally:
            bridge.executor.shutdown(wait=True)
        assert scenario.counter()["total"] == 0
        if received:
            raise RedirectEscape("fixture received an off-allowlist redirect hop")
