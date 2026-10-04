"""Generate a fixture-only G3 sample; no .env, Telegram or live LLM.

Run: python -m tests.report.make_g3_sample
"""

import importlib.util
import tempfile
from pathlib import Path

from scripts.demo_g3 import free_port
from src.operator.report import write_report


def main() -> None:
    """Run G3 and embed its temporary evidence before cleanup."""
    output = Path("docs/08-submission/T-036/report.html").resolve()
    with tempfile.TemporaryDirectory(prefix="report-g3-") as directory:
        # Other builders share the machine. Use an isolated copy of the G3
        # harness on a free fixture port without editing their production script.
        root = Path(__file__).resolve().parents[2]
        harness = Path(directory) / "demo_g3.py"
        source = (root / "scripts/demo_g3.py").read_text(encoding="utf-8")
        source = source.replace("8780", str(free_port())).replace(
            "ROOT = Path(__file__).resolve().parents[1]", f"ROOT = Path({str(root)!r})"
        )
        harness.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("report_g3", harness)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with module.Scenario(Path(directory)) as scenario:
            result = module.exercise(scenario, edit=True, crash="before_claim")
            assert result == {"status": "SUBMITTED_VERIFIED", "submissions": 1}
            write_report(Path(directory), "g3", output)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1
        )
        requests = []
        page.on("request", lambda request: requests.append(request.url))
        page.goto(output.as_uri())
        page.screenshot(path=str(output.with_name("report-preview.png")))
        assert all(url.startswith(("file:", "data:")) for url in requests)
        assert page.locator("script, form").count() == 0
        assert page.locator("img").count() > 0
        browser.close()
    print("G3 sample and offline report preview written under docs/08-submission/T-036")


if __name__ == "__main__":
    main()
