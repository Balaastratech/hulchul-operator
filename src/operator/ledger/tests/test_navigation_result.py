"""Completed navigation observations survive independently of graph checkpoints."""

import pytest

from src.operator.ledger import SQLiteLedger


@pytest.mark.parametrize("moved", [False, True])
def test_navigation_result_is_atomic_durable_and_immutable(tmp_path, moved):
    path = tmp_path / "ledger.sqlite"
    ledger = SQLiteLedger(path)
    assert ledger.claim_action("r", "j", "next", "0")
    assert ledger.action_result("r", "j", "next", "0") is None
    ledger.mark_success("r", "j", "next", "0", result=moved)
    restored = SQLiteLedger(path)
    assert restored.is_done("r", "j", "next", "0")
    assert restored.action_result("r", "j", "next", "0") is moved
    assert not restored.claim_action("r", "j", "next", "0")
    with pytest.raises(ValueError, match="cannot change"):
        restored.mark_success("r", "j", "next", "0", result=not moved)
    assert restored.action_result("r", "j", "next", "0") is moved
