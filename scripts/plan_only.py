#!/usr/bin/env python3
"""scripts/plan_only.py — Planning-half operator demonstration with real Gemini.

Flow:
  intake(goal) -> load_data (DRIVE_FOLDER_ID / fallback sample_data) ->
  enrich postings (fixture server http://127.0.0.1:8780 or local HTML) ->
  select_jobs (real Gemini ranking, deterministic filter, prompt injection quarantine)

Prints:
  - Shortlist with fit reasons
  - Skipped jobs with reasons
  - Quarantined hostile posts (job-1004..1006) with the rule/pattern that caught them
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

# Load single .env per repo architecture rules
MAIN_ENV = Path(r"C:\Balaastra\hulchul-operator\.env")
if MAIN_ENV.exists():
    load_dotenv(MAIN_ENV)
else:
    load_dotenv()

import requests

from src.operator.contracts import DataSnapshot, JobPosting, RunState, RunStatus
from src.operator.data.drive import DrivePublicDataSource
from src.operator.data.local import LocalFolderDataSource
from src.operator.graph.nodes.intake import intake
from src.operator.graph.nodes.load_data import load_data
from src.operator.graph.nodes.select_jobs import select_jobs
from src.operator.graph.runtime import Services
from src.operator.ledger import SQLiteLedger
from src.operator.llm.factory import get_llm_port
from src.operator.policy.allowlist import DomainAllowlist
from src.operator.policy.injection import InjectionClassifier

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


class DummyChannel:
    """Read-only channel sink to capture milestone events."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


def enrich_posting_from_fixture(
    posting: JobPosting,
    fixture_port: int = 8780,
) -> tuple[JobPosting, str | None]:
    """Fetch description and metadata for posting from fixture server or local fixtures.
    
    Returns (enriched_posting, matched_injection_rule).
    """
    m = re.search(r"/jobs/(job-\d+)", posting.url)
    if not m:
        return posting, None

    job_id = m.group(1)
    url = f"http://127.0.0.1:{fixture_port}/jobs/{job_id}"
    html = ""
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            html = resp.text
    except Exception:
        pass

    if not html:
        # Fallback to local hostile fixture file if server is offline or unreachable
        local_fixture = REPO_ROOT / "fixtures" / "job_board_hostile" / f"{job_id}.html"
        if local_fixture.exists():
            html = local_fixture.read_text(encoding="utf-8")

    if not html:
        return posting, None

    # Parse metadata from fixture HTML
    h1_m = re.search(r"<h1>(.*?)</h1>", html)
    title = h1_m.group(1).strip() if h1_m else posting.title

    meta_m = re.search(r'<p class=["\']meta["\']>(.*?)</p>', html)
    meta = meta_m.group(1).strip() if meta_m else ""
    company = meta.split("&middot;")[0].strip() if meta else posting.company

    sal_m = re.search(r"INR\s+([0-9,]+)", meta)
    salary = float(sal_m.group(1).replace(",", "")) if sal_m else posting.salary

    remote = True if "Remote" in meta else (False if meta else posting.remote)

    apply_m = re.search(r'href=[\x22\x27](/ats_[ab]/\?[^\x22\x27]+)[\x22\x27]', html)
    apply_url = f"http://127.0.0.1:{fixture_port}{apply_m.group(1)}" if apply_m else posting.apply_url

    main_m = re.search(r"<main>(.*?)</main>", html, re.DOTALL)
    desc = main_m.group(1) if main_m else html

    enriched = JobPosting(
        job_id=job_id,
        url=url,
        company=company,
        title=title,
        apply_url=apply_url,
        description=desc,
        remote=remote,
        salary=salary,
    )
    return enriched, None


def run_plan_only(
    goal: str = "apply to the 3 best-fit roles under my rules",
    data_source_type: str = "drive_public",
    data_dir: str | Path | None = None,
    fixture_port: int = 8780,
    run_id: str = "plan-run-01",
) -> dict[str, Any]:
    """Execute intake -> load_data -> select_jobs pipeline."""
    # 1. Initialize data source
    if data_source_type == "local_folder":
        folder = Path(data_dir) if data_dir else (REPO_ROOT / "sample_data")
        ds = LocalFolderDataSource(data_dir=folder)
    else:
        folder_id = os.environ.get("DRIVE_FOLDER_ID", "1MtR2aQM2wEBXH_eYkZej07V6hSWXAO5t")
        fallback = Path(data_dir) if data_dir else (REPO_ROOT / "sample_data")
        ds = DrivePublicDataSource(folder_id=folder_id, fallback_dir=fallback)

    # 2. Initialize ports and services
    llm = get_llm_port()
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    ledger = SQLiteLedger(db_file.name)
    allowlist = DomainAllowlist(hosts=frozenset(["127.0.0.1", "localhost"]))
    classifier = InjectionClassifier()
    channel = DummyChannel()

    quarantined_reasons: dict[str, str] = {}

    def scan_injection(text: str) -> bool:
        check = classifier.classify(text)
        if check.quarantined:
            return True
        return False

    services = Services(
        browser=None,  # No browser needed for planning half
        llm=llm,
        data=ds,
        channel=channel,
        ledger=ledger,
        allowlist=allowlist,
        injection_scan=scan_injection,
    )

    # 3. Step 1: Intake
    initial_run = RunState(run_id=run_id, goal=goal)
    state = {"run": initial_run.model_dump(mode="json")}
    state.update(intake(state, services))

    # 4. Step 2: Load Data
    state.update(load_data(state, services))

    # 5. Step 3: Enrich job postings with description & metadata from fixture board
    raw_jobs = state["data"]["jobs"]
    enriched_jobs = []
    policy_skipped_reasons: dict[str, str] = {}
    data_rules = DataSnapshot.model_validate(state["data"]).rules
    blocked_set = {" ".join(c.casefold().split()) for c in data_rules.blocked_companies}

    for item in raw_jobs:
        jp = JobPosting.model_validate(item)
        enriched, _ = enrich_posting_from_fixture(jp, fixture_port=fixture_port)

        # Audit with injection classifier to capture exact rule match for reporting
        if enriched.description:
            c = classifier.classify(enriched.description)
            if c.quarantined:
                quarantined_reasons[enriched.job_id] = (
                    c.detected_patterns[0] if c.detected_patterns else c.reason
                )

        # Compute deterministic filter skip reasons for transparency
        if " ".join(enriched.company.casefold().split()) in blocked_set:
            policy_skipped_reasons[enriched.job_id] = f"Blocked company: '{enriched.company}'"
        elif data_rules.remote_only and enriched.remote is not True:
            policy_skipped_reasons[enriched.job_id] = "Constraint violation: remote_only=True but job is not remote"
        elif data_rules.min_salary is not None and (enriched.salary is None or enriched.salary < data_rules.min_salary):
            policy_skipped_reasons[enriched.job_id] = f"Salary {enriched.salary} is below minimum requirement ({data_rules.min_salary})"
        elif data_rules.target_roles and not any(role.casefold() in enriched.title.casefold() for role in data_rules.target_roles):
            policy_skipped_reasons[enriched.job_id] = f"Title '{enriched.title}' does not match target roles ({data_rules.target_roles})"

        enriched_jobs.append(enriched.model_dump(mode="json"))

    state["data"]["jobs"] = enriched_jobs

    # 6. Step 4: Select Jobs (Deterministic filters + real Gemini ranking)
    state.update(select_jobs(state, services))
    final_run = RunState.model_validate(state["run"])

    # Clean up temp db
    try:
        os.unlink(db_file.name)
    except OSError:
        pass

    return {
        "state": state,
        "run": final_run,
        "selected_ids": state.get("queue", []),
        "quarantined_reasons": quarantined_reasons,
        "policy_skipped_reasons": policy_skipped_reasons,
        "enriched_jobs": {j["job_id"]: JobPosting.model_validate(j) for j in enriched_jobs},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Hulchul Operator — Planning Phase Runner")
    parser.add_argument("--goal", type=str, default="apply to the 3 best-fit roles under my rules")
    parser.add_argument("--data-source", type=str, default="drive_public", choices=["drive_public", "local_folder"])
    parser.add_argument("--data-dir", type=str, default=None, help="Path for local_folder or fallback data")
    parser.add_argument("--port", type=int, default=8780, help="Fixture server port (default: 8780)")
    args = parser.parse_args()

    print("================================================================================")
    print(" Hulchul Job-Apply Operator — Real Planning Phase (Gemini)")
    print("================================================================================")
    print(f"Goal:        '{args.goal}'")
    print(f"Data Source: {args.data_source} (folder: {args.data_dir or 'Drive/sample_data'})")
    print(f"Fixtures:    http://127.0.0.1:{args.port}/jobs/")
    print("--------------------------------------------------------------------------------\n")

    result = run_plan_only(
        goal=args.goal,
        data_source_type=args.data_source,
        data_dir=args.data_dir,
        fixture_port=args.port,
    )

    run: RunState = result["run"]
    selected_ids: list[str] = result["selected_ids"]
    quarantined_reasons: dict[str, str] = result["quarantined_reasons"]
    policy_skipped_reasons: dict[str, str] = result["policy_skipped_reasons"]
    enriched_jobs: dict[str, JobPosting] = result["enriched_jobs"]

    # 1. Print Shortlist
    print("================================================================================")
    print(f" SHORTLIST ({len(selected_ids)} roles selected by Gemini)")
    print("================================================================================")
    for j_id in selected_ids:
        job_state = run.jobs.get(j_id)
        posting = next((p for p in run.shortlist if p.job_id == j_id), None) or enriched_jobs.get(j_id)
        title = posting.title if posting else (job_state.url if job_state else j_id)
        company = posting.company if posting else "Unknown"
        reasons = posting.reasons if (posting and posting.reasons) else ["Best fit according to candidate profile"]
        print(f"\n[SELECTED] {j_id} — {company}: {title}")
        print(f"  URL:    {posting.url if posting else ''}")
        print(f"  Reason: {reasons[0]}")

    # 2. Print Quarantined Jobs (Security Layer)
    print("\n================================================================================")
    print(" QUARANTINED POSTS (Prompt Injection Classifier Defence)")
    print("================================================================================")
    quarantined_found = 0
    for j_id, reason in sorted(quarantined_reasons.items()):
        quarantined_found += 1
        posting = enriched_jobs.get(j_id)
        company = posting.company if posting else "Unknown"
        title = posting.title if posting else "Unknown"
        print(f"\n[QUARANTINED] {j_id} — {company}: {title}")
        print(f"  Triggered Rule: {reason}")
        print(f"  Safety Action:  Excluded from LLM planner prompts; status set to QUARANTINED (D-004)")

    if quarantined_found == 0:
        print("No quarantined postings found in this run.")

    # 3. Print Skipped Jobs
    print("\n================================================================================")
    print(" SKIPPED POSTS (Policy Constraints & Filter Exclusions)")
    print("================================================================================")
    skipped_count = 0
    for j_id, posting in sorted(enriched_jobs.items()):
        if j_id in selected_ids or j_id in quarantined_reasons:
            continue
        skipped_count += 1
        reason = policy_skipped_reasons.get(j_id, "Filtered out by candidate rules or ranking cutoff")
        print(f"\n[SKIPPED] {j_id} — {posting.company}: {posting.title}")
        print(f"  Reason: {reason}")

    if skipped_count == 0:
        print("No jobs skipped by policy filters.")

    print("\n================================================================================")
    print(f" Run Completed: LLM Calls={run.usage.llm_calls}, Status={run.status.value}")
    print("================================================================================")


if __name__ == "__main__":
    main()
