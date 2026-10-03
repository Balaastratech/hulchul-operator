"""Atomic claims, durable submit intent and approval replay boundaries."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.operator.contracts import JobStatus
from src.operator.ledger.sqlite import SQLiteLedger


@pytest.fixture
def ledger(tmp_path):
    return SQLiteLedger(tmp_path / "ledger.sqlite")


def ready(ledger):
    assert ledger.register_application(
        "r", "j", "Fictional Corp", "http://localhost:8000/apply"
    )
    ledger.record_review("r", "j", "b" * 64)


def approve(ledger):
    ready(ledger)
    ledger.record_approval(
        "r", "j", "a" * 64, "b" * 64, datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    assert ledger.consume_approval("r", "j", "a" * 64, "b" * 64)


def test_only_one_concurrent_action_claim_wins(ledger):
    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = list(
            pool.map(lambda _: ledger.claim_action("r", "j", "fill", "name"), range(16))
        )
    assert sum(winners) == 1
    assert not ledger.is_done("r", "j", "fill", "name")
    ledger.mark_success("r", "j", "fill", "name")
    assert ledger.is_done("r", "j", "fill", "name")
    assert not ledger.claim_action("r", "j", "fill", "name")


def test_approval_binding_expiry_replay_and_atomic_gate(ledger):
    ready(ledger)
    ledger.record_approval(
        "r", "j", "a" * 64, "b" * 64, datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    assert not ledger.consume_approval("r", "j", "a" * 64, "c" * 64)
    assert not ledger.consume_approval("other", "j", "a" * 64, "b" * 64)
    assert ledger.get_status("r", "j") == JobStatus.READY_FOR_REVIEW
    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = list(
            pool.map(
                lambda _: ledger.consume_approval("r", "j", "a" * 64, "b" * 64),
                range(16),
            )
        )
    assert sum(winners) == 1
    assert ledger.get_status("r", "j") == JobStatus.APPROVED
    assert not ledger.consume_approval("r", "j", "a" * 64, "b" * 64)


def test_expired_approval_and_changed_review_fail(ledger):
    ready(ledger)
    ledger.record_approval(
        "r", "j", "a" * 64, "b" * 64, datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    assert not ledger.consume_approval("r", "j", "a" * 64, "b" * 64)
    ledger.record_review("r", "j", "c" * 64)
    assert not ledger.consume_approval("r", "j", "a" * 64, "b" * 64)


def test_submit_intent_survives_restart_and_can_never_be_reclaimed(ledger):
    approve(ledger)
    assert ledger.begin_submission("r", "j", "b" * 64)
    restarted = SQLiteLedger(ledger.path)
    assert restarted.get_status("r", "j") == JobStatus.SUBMITTING
    assert not restarted.begin_submission("r", "j", "b" * 64)
    restarted.finish_submission("r", "j", verified=False)
    assert restarted.get_status("r", "j") == JobStatus.SUBMITTED_UNVERIFIED
    assert not restarted.begin_submission("r", "j", "b" * 64)


def test_submit_without_approval_or_for_stale_form_is_rejected(ledger):
    ready(ledger)
    assert not ledger.begin_submission("r", "j", "b" * 64)
    approve(ledger)
    assert not ledger.begin_submission("r", "j", "c" * 64)


def test_duplicate_identity_normalization_and_success_requires_claim(ledger):
    assert ledger.register_application(
        "r", "j", " Fictional  Corp ", "https://example.test/a#fragment"
    )
    assert not ledger.register_application(
        "other", "other", "fictional corp", "https://EXAMPLE.test/a"
    )
    with pytest.raises(ValueError):
        ledger.mark_success("r", "j", "fill", "unclaimed")


def test_action_key_has_no_concatenation_collisions(ledger):
    assert ledger.claim_action("a:b", "c", "fill", "d")
    assert ledger.claim_action("a", "b:c", "fill", "d")


def test_reusing_digest_cannot_reset_consumed_capability(ledger):
    approve(ledger)
    with pytest.raises(ValueError):
        ledger.record_approval(
            "r",
            "j",
            "a" * 64,
            "b" * 64,
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )


def test_finish_cannot_fabricate_submission(ledger):
    ready(ledger)
    with pytest.raises(ValueError):
        ledger.finish_submission("r", "j", verified=True)


def test_gate_write_failure_rolls_back_token_consumption(ledger):
    ready(ledger)
    ledger.record_approval(
        "r", "j", "a" * 64, "b" * 64, datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_gate BEFORE UPDATE OF status ON applications "
            "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        ledger.consume_approval("r", "j", "a" * 64, "b" * 64)
    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute("SELECT used_at FROM approvals").fetchone()[0] is None
        connection.execute("DROP TRIGGER fail_gate")
    assert ledger.consume_approval("r", "j", "a" * 64, "b" * 64)


def test_expiry_is_rechecked_before_durable_submit_intent(tmp_path):
    now = datetime.now(timezone.utc)
    ledger = SQLiteLedger(tmp_path / "expiry.sqlite", clock=lambda: now)
    ready(ledger)
    ledger.record_approval("r", "j", "a" * 64, "b" * 64, now + timedelta(seconds=10))
    assert ledger.consume_approval("r", "j", "a" * 64, "b" * 64)
    now += timedelta(seconds=11)
    assert not ledger.begin_submission("r", "j", "b" * 64)


def test_verified_submission_cannot_be_downgraded(ledger):
    approve(ledger)
    assert ledger.begin_submission("r", "j", "b" * 64)
    ledger.finish_submission("r", "j", verified=True)
    ledger.finish_submission("r", "j", verified=False)
    assert ledger.get_status("r", "j") == JobStatus.SUBMITTED_VERIFIED
