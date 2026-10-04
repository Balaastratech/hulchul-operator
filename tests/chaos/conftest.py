"""Keep fault injection opt-in without editing shared pytest configuration."""

import json
import os
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "chaos: fixture-only worker/request fault injection"
    )


def pytest_collection_modifyitems(items):
    if os.getenv("RUN_CHAOS") != "1":
        for item in items:
            if item.get_closest_marker("chaos") is not None:
                item.add_marker(
                    pytest.mark.skip(reason="set RUN_CHAOS=1; owns port 8780")
                )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Preserve sanitized safety outcomes before pytest cleans temporary cases."""
    report = (yield).get_result()
    directory = item.funcargs.get("tmp_path")
    if report.when == "call" and directory:
        path = directory / "outcome.json"
        if path.exists():
            report.user_properties.append(("chaos_safety", path.read_text()))


def pytest_runtest_logreport(report):
    """Save capability-free row results outside the tracked repository."""
    destination = os.getenv("CHAOS_RESULTS")
    if destination and report.when == "call":
        safety = dict(report.user_properties).get("chaos_safety")
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "test": report.nodeid,
                        "outcome": report.outcome,
                        "xfail": bool(getattr(report, "wasxfail", False)),
                        **({"safety": json.loads(safety)} if safety else {}),
                    }
                )
                + "\n"
            )
