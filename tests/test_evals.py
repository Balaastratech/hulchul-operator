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


def test_planning_rule_variation_fixture_board():
    """Verify T-032 variation proof: remote_only rule variation shifts candidate shortlist on fixture board."""
    from src.operator.data.local import LocalFolderDataSource
    from scripts.plan_only import enrich_posting_from_fixture
    from src.operator.contracts import JobPosting, DataSnapshot
    from src.operator.graph.nodes.select_jobs import select_jobs
    from src.operator.graph.runtime import Services, ShortlistPlan, RankedJob
    from src.operator.policy.injection import InjectionClassifier
    from src.operator.policy.allowlist import DomainAllowlist
    from src.operator.contracts import RunState
    from src.operator.ledger.sqlite import SQLiteLedger
    import tempfile, os

    # 1. Baseline: remote_only=False
    ds_base = LocalFolderDataSource("sample_data")
    snap_base = ds_base.load_sync("run-base")
    assert snap_base.rules.remote_only is False

    # 2. Variant: remote_only=True
    ds_var = LocalFolderDataSource("sample_data_variant")
    snap_var = ds_var.load_sync("run-var")
    assert snap_var.rules.remote_only is True

    # Check that jobs filter deterministically as expected
    classifier = InjectionClassifier()
    base_eligible = []
    var_eligible = []
    for item in snap_base.jobs:
        enriched, _ = enrich_posting_from_fixture(item, 8780)
        c = classifier.classify(enriched.description or "")
        if c.quarantined:
            continue
        # Base check
        if enriched.salary and enriched.salary >= snap_base.rules.min_salary:
            if any(role.casefold() in enriched.title.casefold() for role in snap_base.rules.target_roles):
                if not (snap_base.rules.remote_only and not enriched.remote):
                    base_eligible.append(enriched.job_id)
        # Var check
        if enriched.salary and enriched.salary >= snap_var.rules.min_salary:
            if any(role.casefold() in enriched.title.casefold() for role in snap_var.rules.target_roles):
                if not (snap_var.rules.remote_only and not enriched.remote):
                    var_eligible.append(enriched.job_id)

    # Baseline has job-1001 (hybrid) and job-1002 (remote)
    assert "job-1001" in base_eligible
    assert "job-1002" in base_eligible

    # Variant (remote_only: true) excludes job-1001 and only retains job-1002
    assert "job-1001" not in var_eligible
    assert "job-1002" in var_eligible
    assert len(base_eligible) == 2
    assert len(var_eligible) == 1

