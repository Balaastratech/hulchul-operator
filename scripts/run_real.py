"""Start the real Gemini operator, headed Chrome, fixtures and guarded review plane.

Run from repo root in the pip-install-. venv. Default waits for your review POST.
--auto-fixture is an explicit synthetic human rehearsal: consent + ONE fixture approval.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn

from control_plane.config import load_config
from fixtures.server import FixtureServer, Handler, Store
from src.operator.app.factory import build_services, load_environment
from src.operator.app.review import create_real_app
from src.operator.app.transport import RealTransport
from src.operator.contracts import FieldSpec, Profile
from src.operator.graph import sqlite_graph
from src.operator.graph.adapters import BrowserBridge
from worker.main import Worker, _current_run, _pending_interrupts


class Forms(HTMLParser):
    """Read control-plane forms with the real single-use signed action token."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        values = dict(attrs)
        if tag == "form":
            self.current = {"action": values.get("action"), "fields": {}}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and values.get("name"):
            self.current["fields"][values["name"]] = values.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.current = None


def free_port() -> int:
    """Choose a loopback port for this isolated Chrome instance."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(url: str, timeout: float = 30) -> None:
    """Wait for startup without printing authenticated request URLs."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError("service startup timed out")


def ensure_free(host: str, port: int) -> None:
    """Refuse occupied peer ports before any service starts (Windows exclusive bind)."""
    with socket.socket() as sock:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host, port))


def start_tunnel(port: int, timeout: float = 60) -> tuple[subprocess.Popen, str]:
    """--tunnel: a cloudflared quick tunnel to the local control plane; returns (process, https URL).

    Only the trycloudflare URL is read from cloudflared's output and printed; nothing else it
    writes is shown. The caller must terminate the process (see launch()).
    """
    exe = shutil.which("cloudflared")
    if not exe:
        raise RuntimeError("--tunnel needs cloudflared on PATH (winget install Cloudflare.cloudflared)")
    process = subprocess.Popen(
        [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    found: list[str] = []
    ready = threading.Event()

    def pump() -> None:
        # Keep draining after the URL appears, otherwise a full pipe would stall cloudflared.
        for line in process.stdout or []:
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if match and not found:
                found.append(match.group(0))
                ready.set()

    threading.Thread(target=pump, daemon=True).start()
    if not ready.wait(timeout) or process.poll() is not None:
        process.terminate()
        raise RuntimeError("cloudflared did not report a tunnel URL")
    return process, found[0]


def fixture_human(browser: BrowserBridge, profile: Profile) -> None:
    """Explicit rehearsal actor completes consent/legal controls on local fixtures only."""
    if not browser.allowlist.permits_submission(browser.page.url):
        raise PermissionError("synthetic human actor is fixture-only")
    # These are the fixture human's explicit actions, never planner/operator authority.
    for selector in ("[name=privacy_consent]", "[name=consent_terms]"):
        item = browser.page.locator(selector)
        if item.count() and item.is_visible():
            item.check(force=True)
    for selector in ("[name=work_authorization][value=authorized]",):
        item = browser.page.locator(selector)
        if item.count() and item.is_visible():
            item.check()
    sponsor = browser.page.locator("#sponsor-no")
    if sponsor.count() and sponsor.is_visible():
        sponsor.click()
    item = browser.page.locator("select[name=work_eligibility]")
    if item.count() and item.is_visible():
        options = item.locator("option").all_text_contents()
        authorized = [
            o
            for o in options
            if "authorized" in o.casefold() and "not" not in o.casefold()
        ]
        if len(authorized) == 1 and profile.work_authorization:
            item.select_option(label=authorized[0])


def child(args: argparse.Namespace) -> int:
    """Run real Worker/checkpoints; preserve deepest gate state for the launcher."""
    services = build_services()
    prefix = os.environ["CP_LOCAL_URL"] + f"/api/worker/runs/{args.run_id}"
    transport = RealTransport(
        prefix + "/commands",
        prefix + "/ack",
        prefix + "/heartbeat",
        authorization="Bearer " + os.environ["CP_WORKER_TOKEN"],
    )
    directory = args.state_dir
    try:
        if args.public_job_id:
            from src.operator.app.public import public_proof

            result = public_proof(
                services,
                args.run_id + "-public",
                args.public_job_id,
                os.environ["REAL_CDP"],
                directory,
            )
            return 0 if result["state"] == "FILL_ONLY_REVIEW" else 2
        with sqlite_graph(services, directory / "checkpoints.sqlite") as graph:
            worker = Worker(
                graph, services, transport, args.run_id, directory / "commands.sqlite"
            )

            def resolve_field(command):
                saved = graph.get_state(worker.config, subgraphs=True)
                if not saved.values:
                    return None
                current = _current_run(saved)
                target = current.jobs.get(command.job_id)
                return (
                    next(
                        (
                            FieldSpec.model_validate(f)
                            for f in target.fields
                            if f.key == command.field_key
                        ),
                        None,
                    )
                    if target
                    else None
                )

            transport.resolve_field = resolve_field
            worker.start_or_resume(args.goal, os.environ["REAL_CDP"])
            while True:
                snapshot = graph.get_state(worker.config, subgraphs=True)
                run = _current_run(snapshot)
                gates = _pending_interrupts(snapshot)
                state = {
                    "run": run.model_dump(mode="json"),
                    "pending": [g.value for g in gates],
                    "terminal": not bool(snapshot.next),
                }
                temp = directory / "state.tmp"
                temp.write_text(json.dumps(state), encoding="utf-8")
                os.replace(temp, directory / "state.json")
                if not snapshot.next:
                    (directory / "report.json").write_text(
                        run.model_dump_json(indent=2), encoding="utf-8"
                    )
                    print("Report: " + str(directory / "report.json"), flush=True)
                    print(
                        "Gemini usage: " + services.llm.get_usage().model_dump_json(),
                        flush=True,
                    )
                    return 0
                if (directory / "fixture-human.request").exists():
                    (directory / "fixture-human.request").unlink()
                    services.call(
                        services.browser._call(
                            fixture_human,
                            services.browser,
                            services.data.loaded.profile,
                        )
                    )
                    (directory / "fixture-human.done").write_text(
                        "done", encoding="utf-8"
                    )
                worker.tick()
                time.sleep(0.5)
    finally:
        services.call(services.channel.close())
        if services.browser.page is not None:
            services.call(services.browser.disconnect())
        else:
            services.browser.executor.shutdown(wait=True)
        services.close()


def launch(args: argparse.Namespace) -> int:
    """Own and clean up only the processes/data created by this real run."""
    load_environment()
    directory = args.state_dir.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError(
            "choose a fresh --state-dir; never overwrite a previous real run"
        )
    directory.mkdir(parents=True, exist_ok=True)
    os.environ["REAL_STATE_DIR"] = str(directory)
    os.environ["CP_DB_PATH"] = str(directory / "cp.sqlite")
    os.environ["CP_ENV"] = "dev"
    local_url = f"http://127.0.0.1:{args.cp_port}"
    os.environ["CP_LOCAL_URL"] = local_url
    if args.tunnel and args.use_public_url:
        raise ValueError("--tunnel already sets the public URL; drop --use-public-url")
    if not args.use_public_url:
        os.environ["CP_BASE_URL"] = local_url
    # In-memory per-run secrets isolate local rehearsal; canonical .env remains untouched.
    os.environ["CP_SIGNING_KEY"] = secrets.token_urlsafe(48)
    os.environ["CP_WORKER_TOKEN"] = secrets.token_urlsafe(48)
    os.environ.pop("CP_SIGNING_KEY_PREVIOUS", None)
    if args.no_telegram:
        os.environ["REAL_NO_TELEGRAM"] = "1"
    if args.data_source:
        os.environ["DATA_SOURCE"] = args.data_source
    os.environ["REAL_INCLUDE_PUBLIC"] = (
        "1" if args.include_public or args.public_job_id else "0"
    )
    os.environ["REAL_FIXTURE_HOST"] = args.fixture_host
    ensure_free(args.fixture_host, 8780)
    ensure_free("127.0.0.1", args.cp_port)
    tunnel = None
    fixture = chrome = process = server = app = None
    cp_thread = None
    try:
        if args.tunnel:
            tunnel, public_url = start_tunnel(args.cp_port)
            os.environ["CP_BASE_URL"] = public_url
            print("Public control plane URL: " + public_url, flush=True)
        config = load_config()
        handler = type(
            "RealRunHandler",
            (Handler,),
            {"store": Store(directory / "fixture" / "submissions.json")},
        )
        fixture = FixtureServer((args.fixture_host, 8780), handler)
        threading.Thread(target=fixture.serve_forever, daemon=True).start()
        app = create_real_app(config)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=args.cp_port,
                log_level="error",
                access_log=False,
                timeout_graceful_shutdown=2,
            )
        )
        cp_thread = threading.Thread(target=server.run, daemon=True)
        cp_thread.start()
        wait_http(local_url + "/r/startup")
        cdp_port = free_port()
        os.environ["REAL_CDP"] = f"http://127.0.0.1:{cdp_port}"
        chrome_path = (
            os.environ.get("CHROME_PATH")
            or shutil.which("chrome")
            or r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        )
        command = [
            chrome_path,
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={directory / 'chrome'}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if args.headless:
            command.append("--headless=new")
        chrome = subprocess.Popen(
            command + ["about:blank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        wait_http(os.environ["REAL_CDP"] + "/json/version")
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                "--goal",
                args.goal,
                "--run-id",
                args.run_id,
                "--state-dir",
                str(directory),
            ],
            cwd=ROOT,
        )
        print(
            f"Real run {args.run_id}; fixtures http://{args.fixture_host}:8780; control plane {config.base_url}",
            flush=True,
        )
        print("State directory: " + str(directory), flush=True)
        approved = set()
        handled = set()
        deadline = time.monotonic() + args.timeout
        with httpx.Client(base_url=local_url, timeout=15) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                state_path = directory / "state.json"
                if not state_path.exists():
                    time.sleep(0.5)
                    continue
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state["terminal"]:
                    process.wait(timeout=30)
                    break
                run = state["run"]
                job_id = run["active_job_id"]
                job = run["jobs"].get(job_id)
                if not args.auto_fixture or not job or not state["pending"]:
                    time.sleep(0.5)
                    continue
                # No synthetic human act or automatic approval on a public site, even with --auto-fixture.
                if (
                    not app.state.submission_policy.permits(args.run_id, job_id)
                    and job["status"] == "READY_FOR_REVIEW"
                ):
                    print(
                        "Public gate retained; approval disabled (D-014).", flush=True
                    )
                    break
                if (
                    urlsplit(job["url"]).hostname != args.fixture_host
                    or urlsplit(job["url"]).port != 8780
                ):
                    print(
                        "Public human gate retained; use visible browser to finish.",
                        flush=True,
                    )
                    break
                gate = state["pending"][0]
                key = (
                    job_id,
                    gate["kind"],
                    job.get("review_snapshot_hash"),
                    gate.get("field_key"),
                )
                if key in handled:
                    time.sleep(0.5)
                    continue
                view = app.state.tokens.mint("view", args.run_id, job=job_id)
                page = client.get(f"/r/{args.run_id}/{job_id}", params={"t": view})
                forms = Forms(page.text).forms
                if gate["kind"] == "handoff":
                    (directory / "fixture-human.done").unlink(missing_ok=True)
                    (directory / "fixture-human.request").write_text(
                        "explicit synthetic human actor", encoding="utf-8"
                    )
                    wait = time.monotonic() + 15
                    while (
                        not (directory / "fixture-human.done").exists()
                        and time.monotonic() < wait
                    ):
                        time.sleep(0.1)
                    if not (directory / "fixture-human.done").exists():
                        raise RuntimeError(
                            "fixture human completion failed; no done command queued"
                        )
                    action = "handoff_done"
                elif gate["kind"] == "review":
                    action = "approve" if not approved else "reject"
                elif gate["kind"] == "answer":
                    field = next(
                        f for f in job["fields"] if f["key"] == gate["field_key"]
                    )
                    if (
                        field["type"] == "radio"
                        and field["label"].casefold() in {"yes", "yes, authorized"}
                        and any(
                            term in field["group"].casefold()
                            for term in ("eligible to work", "authorized to work")
                        )
                    ):
                        action = "answer"
                    else:
                        raise RuntimeError(
                            "fixture needs an explicit unknown answer; use interactive mode"
                        )
                else:
                    raise RuntimeError(
                        "fixture needs an explicit unknown answer; use interactive mode"
                    )
                candidates = [f for f in forms if f["action"] == "/api/" + action]
                if not candidates:
                    raise RuntimeError("review action unavailable: " + action)
                if action == "answer":
                    candidates[0]["fields"]["value"] = (
                        "true"  # CP text -> graph boolean for this exact field
                    )
                response = client.post(
                    "/api/" + action,
                    json=candidates[0]["fields"],
                    headers={
                        "Origin": config.base_origin,
                        "Sec-Fetch-Site": "same-origin",
                    },
                )
                if response.status_code != 200:
                    raise RuntimeError(f"review POST refused: {response.status_code}")
                handled.add(key)
                if action == "approve":
                    approved.add(job_id)
                print("Synthetic fixture human POST " + action, flush=True)
                time.sleep(0.5)
            else:
                raise RuntimeError(
                    "real run timeout; checkpoint and browser state saved"
                )
        if process.poll() is None and not args.auto_fixture:
            process.wait(timeout=30)
        counter = fixture.RequestHandlerClass.store.snapshot()
        print(
            "Fixture counter: "
            + json.dumps(
                {
                    "total": counter["total"],
                    "duplicate_posts": counter["duplicate_posts"],
                }
            ),
            flush=True,
        )
        if process.poll() is not None and process.returncode != 0:
            raise RuntimeError(
                "worker exited with error; inspect saved timeline/checkpoint"
            )
        if args.auto_fixture:
            report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
            verified = [
                j
                for j in report["jobs"].values()
                if j["status"] == "SUBMITTED_VERIFIED"
            ]
            if (
                counter["total"] != 1
                or counter["duplicate_posts"]
                or len(verified) != 1
            ):
                raise RuntimeError(
                    "exactly-one verified fixture submission assertion failed"
                )
            print(
                "REAL PASS: exactly ONE fixture submission, verified; other selected jobs rejected by rehearsal actor.",
                flush=True,
            )
        if args.public_job_id:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--child",
                    "--goal",
                    args.goal,
                    "--run-id",
                    args.run_id,
                    "--state-dir",
                    str(directory),
                    "--public-job-id",
                    args.public_job_id,
                ],
                cwd=ROOT,
            )
            process.wait(timeout=300)
            if process.returncode:
                raise RuntimeError(
                    "public proof could not reach a live form; see public-proof.json"
                )
        return 0
    finally:
        for owned in (process, chrome, tunnel):
            if owned and owned.poll() is None:
                owned.terminate()
                try:
                    owned.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    owned.kill()
                    owned.wait(timeout=5)
        if server:
            server.should_exit = True
            cp_thread.join(timeout=10)
        if fixture:
            fixture.shutdown()
            fixture.server_close()


def main() -> int:
    """One command starts a fresh real run; Ctrl-C preserves local checkpoint artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--goal", default="Apply to the 3 best-fit roles under my rules"
    )
    parser.add_argument("--run-id", default="real-" + time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--cp-port", type=int, default=8790)
    parser.add_argument(
        "--fixture-host",
        choices=["127.0.0.1", "127.0.0.2"],
        default="127.0.0.1",
        help="use 127.0.0.2 when another agent owns 127.0.0.1:8780",
    )
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--data-source", choices=["local_folder", "drive_public"])
    parser.add_argument("--include-public", action="store_true")
    parser.add_argument(
        "--public-job-id",
        help="after fixture run, explicitly probe public-job-001..003 fill-only",
    )
    parser.add_argument(
        "--use-public-url",
        action="store_true",
        help="CP_BASE_URL tunnel must forward to --cp-port",
    )
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help="start a cloudflared quick tunnel to --cp-port, use its URL as CP_BASE_URL, stop it on exit",
    )
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument(
        "--headless", action="store_true", help="default Chrome is headed"
    )
    parser.add_argument(
        "--auto-fixture",
        action="store_true",
        help="explicit synthetic human, exactly ONE fixture approval",
    )
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.state_dir = args.state_dir or ROOT / "runs" / args.run_id
    try:
        return child(args) if args.child else launch(args)
    except KeyboardInterrupt:
        print("Stopped. Local checkpoint/data/evidence retained.", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
