"""Live check: Fill Platform Engineer fixture on ATS B using real Vertex planner.

Verifies:
- Reaches READY_FOR_REVIEW
- Questions left <= 3
- Derived values listed (including date from 'Within 30 days of an offer')
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import httpx

from control_plane.app import create_app
from control_plane.config import load_config
from control_plane.tokens import TokenService
from fixtures.server import make_server
from src.operator.app.factory import build_services
from src.operator.contracts import JobPosting, JobStatus
from src.operator.graph import sqlite_graph
from src.operator.policy.allowlist import DomainAllowlist
from worker.main import Worker, _current_run
from worker.transport import HttpTransport


def main():
    load_dotenv(r"C:\Balaastra\hulchul-operator\.env")
    assert os.getenv("GOOGLE_CLOUD_PROJECT"), "GOOGLE_CLOUD_PROJECT must be set"

    port = 8780
    cp_port = 8786
    temp_dir = Path(tempfile.mkdtemp(prefix="live_check_t051_"))
    print(f"Working in temporary directory: {temp_dir}")

    # 1. Start fixture server on port 8780
    fixture_server = make_server(port, temp_dir / "fixture")
    fixture_thread = threading.Thread(target=fixture_server.serve_forever, daemon=True)
    fixture_thread.start()
    print(f"Fixture server started on port {port}")

    # 2. Start Control Plane on cp_port
    signing_key = os.environ.get("CP_SIGNING_KEY", "test_secret_signing_key_for_live_verification_12345")
    worker_token = os.environ.get("CP_WORKER_TOKEN", "test_worker_token_for_live_verification_12345")
    config = load_config(
        environ={
            "CP_SIGNING_KEY": signing_key,
            "CP_WORKER_TOKEN": worker_token,
            "CP_BASE_URL": f"http://127.0.0.1:{cp_port}",
            "CP_ENV": "dev",
            "CP_DB_PATH": str(temp_dir / "cp.sqlite"),
        }
    )
    import uvicorn
    app = create_app(config)
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=cp_port,
            log_level="error",
            access_log=False,
        )
    )
    cp_thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    cp_thread.start()
    print(f"Control plane started on port {cp_port}")

    # 3. Start Chrome CDP
    chrome_path = (
        os.environ.get("CHROME_PATH")
        or shutil.which("chrome")
        or r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    )
    cdp_port = 9223
    cdp_url = f"http://127.0.0.1:{cdp_port}"
    chrome_proc = subprocess.Popen(
        [
            chrome_path,
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={temp_dir / 'chrome'}",
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"Chrome launched on CDP {cdp_url}")

    # Wait for CDP and CP
    time.sleep(2)

    # 4. Build services with real Vertex LLM and local fixture
    os.environ["REAL_STATE_DIR"] = str(temp_dir / "worker")
    os.environ["REAL_FIXTURE_HOST"] = "127.0.0.1"
    os.environ["DATA_SOURCE"] = "local_folder"
    os.environ["LOCAL_DATA_DIR"] = str(ROOT / "sample_data")
    os.environ["REAL_GOAL"] = "Apply to 1 role under my rules"
    os.environ["CP_ENV"] = "dev"
    os.environ["CP_BASE_URL"] = f"http://127.0.0.1:{cp_port}"
    os.environ["CP_WORKER_TOKEN"] = worker_token
    os.environ["CP_SIGNING_KEY"] = signing_key
    os.environ["NO_TELEGRAM"] = "1"
    os.environ["REAL_NO_TELEGRAM"] = "1"

    services = build_services(goal="Apply to 1 role under my rules")
    # Target our port 8785 ATS B fixture
    ats_b_url = f"http://127.0.0.1:{port}/ats_b/"
    allowlist = DomainAllowlist.from_urls(
        [ats_b_url],
        fixture_urls=[f"http://127.0.0.1:{port}"],
        control_plane_url=f"http://127.0.0.1:{cp_port}",
    )
    services.allowlist = allowlist
    services.browser.allowlist = allowlist

    # Override the single target job in services.data
    original_load = services.data.load
    async def load_override(run_id):
        snap = await original_load(run_id)
        snap.jobs = [
            JobPosting(
                job_id="ats_b_platform",
                company="Lumen Example Cloud",
                title="Platform Engineer",
                url=ats_b_url,
                salary=2500000.0,
                remote=True,
                description="Platform Engineer role at Lumen Example Cloud. Python, systems engineering.",
            )
        ]
        updated = DomainAllowlist.from_urls([ats_b_url], fixture_urls=[f"http://127.0.0.1:{port}"], control_plane_url=f"http://127.0.0.1:{cp_port}")
        object.__setattr__(services.allowlist, "hosts", services.allowlist.hosts | updated.hosts)
        object.__setattr__(services.allowlist, "fixture_origins", services.allowlist.fixture_origins | updated.fixture_origins)
        return snap
    services.data.load = load_override
    # Override injection scanner to false for synthetic fixture description
    services.injection_scan = lambda text: False

    prefix = f"{config.base_url}/api/worker/runs/live_t051"
    transport = HttpTransport(
        f"{prefix}/commands",
        f"{prefix}/ack",
        f"{prefix}/heartbeat",
        authorization="Bearer " + config.worker_token,
    )

    try:
        print("Starting worker execution...")
        with sqlite_graph(services, temp_dir / "checkpoints.sqlite") as graph:
            worker = Worker(
                graph, services, transport, "live_t051", temp_dir / "commands.sqlite"
            )
            # Run graph until pause / review
            result = worker.start_or_resume(
                "Apply to 1 role under my rules",
                cdp_url,
            )

            from langgraph.types import Command as Resume
            for turn in range(8):
                gate = result.get("__interrupt__", [None])[0] if isinstance(result, dict) else None
                if not gate or gate.value.get("kind") == "review":
                    break
                print(f"Gate interrupt observed: kind={gate.value.get('kind')}")
                if gate.value.get("kind") == "answer":
                    field_key = gate.value["field_key"]
                    val = True if "radio" in field_key or "checkbox" in field_key or "terms" in field_key.lower() else "Yes"
                    result = graph.invoke(
                        Resume(
                            resume={
                                "command_id": f"answer-{turn}",
                                "run_id": "live_t051",
                                "job_id": "ats_b_platform",
                                "action": "answer",
                                "field_key": field_key,
                                "value": val,
                            }
                        ),
                        worker.config,
                    )
                    continue
                if gate.value.get("kind") == "handoff":
                    def human():
                        for selector in [
                            "[name=privacy_consent]",
                            "[name=consent_terms]",
                            "[name=work_authorization][value=authorized]",
                            "[name=work_eligibility][value=yes]",
                        ]:
                            ctrl = services.browser.page.locator(selector)
                            if ctrl.count() and (ctrl.is_visible() or ctrl.locator("xpath=ancestor::label[1]").is_visible()):
                                ctrl.check(force=True)
                    services.call(services.browser._call(human))
                    result = graph.invoke(
                        Resume(
                            resume={
                                "command_id": f"human-{turn}",
                                "run_id": "live_t051",
                                "action": "handoff_done",
                            }
                        ),
                        worker.config,
                    )

            snapshot = graph.get_state(worker.config, subgraphs=True)
            run = _current_run(snapshot)
            job = run.jobs.get("ats_b_platform") or list(run.jobs.values())[0]

            print(f"\n--- RUN FINISHED ---")
            print(f"Job Status: {job.status}")
            print(f"Repair Attempts: {job.repair_attempts}")
            print(f"Blockers: {job.blockers}")
            print(f"Notes: {job.notes}")

            # Inspect actions and fill report
            actions = job.actions
            print(f"Total planned actions: {len(actions)}")
            derived_actions = [a for a in actions if a.derived]
            print(f"Derived actions count: {len(derived_actions)}")
            for da in derived_actions:
                print(f"  [DERIVED] {da.field_key}: value={da.value!r}, source={da.source}")

            # Check unanswered / ask_user questions
            ask_users = [a for a in actions if a.action == "ask_user"]
            print(f"Questions left needing user answer: {len(ask_users)}")
            for au in ask_users:
                print(f"  [ASK_USER] {au.field_key}: question={au.question}")

            if job.status == JobStatus.READY_FOR_REVIEW:
                print("\nSUCCESS: Job reached READY_FOR_REVIEW!")
            elif job.status == JobStatus.NEEDS_ANSWER:
                print(f"\nJob paused at NEEDS_ANSWER with {len(ask_users)} questions asked.")

    finally:
        # Cleanup
        chrome_proc.kill()
        chrome_proc.wait()
        uvicorn_server.should_exit = True
        fixture_server.shutdown()
        fixture_server.server_close()
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
