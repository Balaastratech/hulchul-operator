"""Timeout regression: leave fixture unsubmitted and release SQLite on Windows."""
import sqlite3
from types import SimpleNamespace

import pytest

from deploy import phone_proof


def test_phone_timeout_records_no_approval_and_closes_database(tmp_path, monkeypatch):
    db_path = tmp_path / "cp.sqlite"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE commands(action TEXT)")
    db.commit()
    db.close()
    timeline = iter([0, 0, 601, 601])
    monkeypatch.setattr(phone_proof, "time", SimpleNamespace(monotonic=lambda: next(timeline), sleep=lambda _: None))
    results = []
    monkeypatch.setattr(phone_proof, "persist", results.append)
    monkeypatch.setattr(phone_proof, "APPROVALS", [])
    scenario = SimpleNamespace(reach_review=lambda: None,
                               config=SimpleNamespace(db_path=db_path), counter=lambda: {"total": 0})
    with pytest.raises(RuntimeError, match="no submission"):
        phone_proof.phone_exercise(scenario)
    assert results[0]["approval_commands"] == 0 and results[0]["submissions"] == 0
    db_path.unlink()  # fails on Windows when an exception retains a live connection
