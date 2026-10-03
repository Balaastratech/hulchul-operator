"""Automated pytest test suite verifying T-023 evals and TP-12."""

import pytest
from evals.evaluator import EvalSuite
from evals.fixtures import CANDIDATE_VARIANTS, SYNTHETIC_JOB_FIXTURES
from evals.run_evals import run_all_evals


def test_evals_suite_full_run():
    """Verify that all eval cases in the T-023 matrix pass."""
    results = run_all_evals()
    assert len(results) >= 13

    for r in results:
        assert r.passed, f"Eval case {r.test_id} ({r.name}) failed: {r.details}"


def test_tp12_rule_change_dynamism():
    """Verify TP-12: Drive rule change produces different shortlist with identical code."""
    suite = EvalSuite()
    result = suite.evaluate_tp12_rule_change()
    assert result.passed
    assert "V1=5, V2=8, V3=3" in result.actual_outcome


def test_negative_safety_gates():
    """Verify all 5 negative safety gates pass."""
    suite = EvalSuite()
    gates = suite.evaluate_negative_gates()
    assert len(gates) == 5
    for g in gates:
        assert g.passed, f"Negative gate {g.test_id} failed: {g.details}"
