"""Real Chrome opt-in: $env:RUN_G3='1'; python -m pytest tests/integration/test_g3_click_to_submit.py -m live -q

Owns ATS port 8780 (must be free); CP/CDP ports and all credentials are generated.
No .env, external ATS, Telegram, or real LLM is used. Each worker is a new process.
"""

import os

import pytest

from scripts.demo_g3 import Scenario, exercise


@pytest.mark.skipif(
    os.getenv("RUN_G3") != "1", reason="real browser opt-in: set RUN_G3=1"
)
@pytest.mark.live
@pytest.mark.parametrize(
    "edit,crash", [(False, None), (True, "before_claim"), (False, "after_claim")]
)
def test_g3_click_to_submit(tmp_path, edit, crash):
    """Real HTTP gates, edits, abrupt worker death, recovery and fixture counter."""
    with Scenario(tmp_path) as scenario:
        exercise(scenario, edit=edit, crash=crash)
