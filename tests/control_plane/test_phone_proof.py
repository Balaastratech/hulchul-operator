"""Timeout regression: leave fixture unsubmitted and release SQLite on Windows."""
import sqlite3
from types import SimpleNamespace

import pytest

from deploy import phone_proof


def test_phone_fixture_address_is_exclusive_and_authority_is_exact(tmp_path):
    server = phone_proof.isolated_server(0, tmp_path)
    try:
        assert server.server_address[0] == phone_proof.FIXTURE_HOST
        with pytest.raises(OSError):
            phone_proof.isolated_server(server.server_address[1], tmp_path / "other")
    finally:
        server.server_close()
    allowed = phone_proof.IsolatedAllowlist.from_urls(
        ["http://127.0.0.1:8780/ats_a/"], fixture_urls=["http://127.0.0.1:8780"])
    assert allowed.permits_submission(phone_proof.FIXTURE_BASE + "/ats_a/")
    assert not allowed.permits_submission("http://127.0.0.1:8780/ats_a/")
    assert not allowed.permits_submission("https://real-employer.example/submit")


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
