"""G3 fixture-only rehearsal. Run: python scripts/demo_g3.py --headless --no-telegram.

Default is headed system Chrome. CP_BASE_URL may name a tunnel to --cp-port.
All CP credentials are generated here; only optional Telegram settings use ENV_FILE.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn

from control_plane.app import create_app
from control_plane.config import load_config
from control_plane.tokens import TokenService
from fixtures.server import make_server
from src.operator.browser.cdp import CDPBrowserManager
from src.operator.channels.web import HttpSink, WebChannel
from src.operator.contracts import Answer, AnswerLibrary, FillAction, Goal
from src.operator.graph import Services, sqlite_graph
from src.operator.graph.adapters import BrowserBridge
from src.operator.graph.runtime import AnswerPlan, ShortlistPlan
from src.operator.graph.tests.fakes import FakeData
from src.operator.ledger import SQLiteLedger
from src.operator.policy.allowlist import DomainAllowlist
from worker.main import Worker, _current_run
from worker.transport import HttpTransport


def free_port() -> int:
    """Reserve an OS-selected loopback port briefly."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, description: str, timeout: float = 30) -> None:
    """Bound every startup/barrier wait; suppress response URLs and credentials."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, httpx.HTTPError):
            pass
        time.sleep(0.1)
    raise RuntimeError(description + " timed out")


class Forms(HTMLParser):
    """Extract real review-page forms rather than manufacturing action tokens."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action"), "fields": {}}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and attrs.get("name"):
            self.current["fields"][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class FixtureData(FakeData):
    """Synthetic candidate with explicit answer-library provenance."""

    def __init__(self, directory: Path):
        self.directory = directory

    async def load(self, run_id):
        data = await super().load(run_id)
        data.profile.phone = "+919000000000"
        data.jobs[0].url = "http://127.0.0.1:8780/ats_a/"
        resume = self.directory / "synthetic.pdf"
        data.resume_path = str(resume)
        data.resume_hash = hashlib.sha256(resume.read_bytes()).hexdigest()
        data.answer_library = AnswerLibrary(
            answers=[
                Answer(pattern=key, answer=value, source="synthetic fixture profile")
                for key, value in [
                    ("first", "Synthetic"),
                    ("last", "Example"),
                    ("country", "India"),
                ]
            ]
        )
        return data


class FixturePlanner:
    """Deterministic LLM port; unknown required controls still go to a human gate."""

    def __init__(self, directory: Path):
        self.directory = directory

    async def structured(self, prompt, response_model):
        with (self.directory / "planning.txt").open("a", encoding="utf-8") as stream:
            stream.write(response_model.__name__ + "\n")
        if response_model is Goal:
            return Goal(mode="normal")
        if response_model is ShortlistPlan:
            return ShortlistPlan(
                jobs=[{"job_id": "fixture", "score": 1, "reason": "G3 fixture"}]
            )
        data = json.loads(
            prompt.split("<untrusted_data>")[1].split("</untrusted_data>")[0]
        )
        actions = []
        for field in data["fields"]:
            label = field["label"].casefold()
            kind, value, source = "skip", None, None
            if "first name" in label:
                kind, value, source = "fill", "Synthetic", "answers.first"
            elif "last name" in label:
                kind, value, source = "fill", "Example", "answers.last"
            elif field["type"] in {"email", "tel"}:
                key = "email" if field["type"] == "email" else "phone"
                kind, value, source = "fill", data["profile"][key], "profile." + key
            elif field["type"] == "select" and "country" in label:
                kind, value, source = "select", "India", "answers.country"
            elif field["type"] == "file" and "resume" in label:
                kind, value, source = "upload_resume", data["resume_path"], "resume"
            elif field["required"]:
                kind = "ask_user"
            actions.append(
                FillAction(
                    field_key=field["key"],
                    action=kind,
                    value=value,
                    source=source,
                    question="Synthetic human answer" if kind == "ask_user" else None,
                )
            )
        return AnswerPlan(actions=actions)


class ObservedBrowser(BrowserBridge):
    """Journal real mutating operations and capture actual confirmation evidence."""

    def __init__(self, allowed: DomainAllowlist, directory: Path):
        super().__init__(CDPBrowserManager(), allowed, directory / "evidence")
        self.directory = directory

    def record(self, operation: str) -> None:
        """Persist operation names, never credentials or candidate values."""
        with (self.directory / "operations.txt").open("a", encoding="utf-8") as stream:
            stream.write(operation + "\n")

    async def attach(self, endpoint: str, target_id: str | None = None) -> None:
        await super().attach(endpoint, target_id)
        # Headed screenshots/read-back must target the ATS tab even while the
        # review page stays open for the video in the same persistent Chrome.
        await self._call(self.page.bring_to_front)

    async def navigate(self, url: str) -> None:
        self.record("navigate")
        await super().navigate(url)

    async def execute(self, action: FillAction):
        self.record("execute")
        return await super().execute(action)

    async def submit(self) -> None:
        self.record("submit")
        await super().submit()

    async def verify_submission(self):
        result = await super().verify_submission()
        return result.model_copy(update={"evidence": await self.capture_evidence()})


class RecordingChannel:
    """Record milestones and explicitly translate graph review payload for WebChannel."""

    def __init__(self, directory: Path, base: str, auth: str):
        self.directory = directory
        self.sink = HttpSink(base, auth, attempts=1)
        self.web = WebChannel(self.sink)

    async def emit(self, event):
        payload = dict(event.payload)
        if event.event_id in {"E07", "E08"}:
            payload["review_snapshot"] = payload.pop("review")
        await self.web.emit(event.model_copy(update={"payload": payload}))
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")


def child_worker(directory: Path, mode: str, crash: str | None) -> None:
    """Run the actual Worker in a separately killable process with durable checkpoints."""
    base = os.environ["G3_CP_LOCAL"]
    public = os.environ["G3_CP_PUBLIC"]
    auth = "Bearer " + os.environ["G3_WORKER_TOKEN"]
    tokens = TokenService(os.environ["G3_SIGNING_KEY"].encode())
    allowed = DomainAllowlist.from_urls(
        ["http://127.0.0.1:8780/ats_a/"],
        fixture_urls=["http://127.0.0.1:8780"],
        control_plane_url=public,
    )
    browser = ObservedBrowser(allowed, directory)
    channel = RecordingChannel(directory, base, auth)
    services = Services(
        browser=browser,
        llm=FixturePlanner(directory),
        data=FixtureData(directory),
        channel=channel,
        ledger=SQLiteLedger(directory / "ledger.sqlite"),
        allowlist=allowed,
        submission_urls=browser.submission_urls,
        restore_browser=browser.restore,
        target_id=browser.target_id,
        review_url=lambda run, job, digest: (
            f"{public}/r/{run}/{job}?t={quote(tokens.mint('view', run, job=job))}"
        ),
    )
    prefix = base + "/api/worker/runs/g3"
    transport = HttpTransport(
        prefix + "/commands", prefix + "/ack", prefix + "/heartbeat", authorization=auth
    )
    try:
        with sqlite_graph(services, directory / "checkpoints.sqlite") as graph:
            worker = Worker(
                graph, services, transport, "g3", directory / "commands.sqlite"
            )
            worker.start_or_resume(
                "Apply to the synthetic local fixture", os.environ["G3_CDP"]
            )
            if mode == "human":
                # Explicit synthetic human actor, never an operator policy bypass on an employer.
                def human():
                    browser.page.locator("[name=privacy_consent]").check(force=True)
                    browser.page.locator(
                        "[name=work_authorization][value=authorized]"
                    ).check()
                    browser.page.locator("#sponsor-no").click()

                services.call(browser._call(human))
            if crash:

                async def barrier(*args):
                    (directory / "crash-ready").write_text(crash, encoding="utf-8")
                    while True:
                        await asyncio.sleep(0.1)

                if crash == "before_claim":
                    browser.read_review = barrier
                else:
                    browser.submit = barrier
            if mode != "start":
                worker.tick()
            snapshot = graph.get_state(worker.config, subgraphs=True)
            run = _current_run(snapshot)
            (directory / "state.json").write_text(
                run.model_dump_json(), encoding="utf-8"
            )
    finally:
        services.call(channel.sink._client.aclose())
        services.call(browser.disconnect())
        services.close()


class Scenario:
    """Own local fixture, uvicorn, Chrome and independently restarted worker processes."""

    def __init__(
        self,
        directory: Path,
        *,
        headed: bool = False,
        public_url: str | None = None,
        cp_port: int = 0,
    ):
        self.directory = directory
        self.headed = headed
        self.cp_port = cp_port or free_port()
        self.base = f"http://127.0.0.1:{self.cp_port}"
        self.public = (public_url or self.base).rstrip("/")
        self.origin = f"{urlsplit(self.public).scheme}://{urlsplit(self.public).netloc}"
        self.children = []
        self.chrome = None
        self.fixture = None
        self.uvicorn = None
        self.client = httpx.Client(base_url=self.base, timeout=10)
        self.config = load_config(
            environ={
                "CP_SIGNING_KEY": secrets.token_urlsafe(48),
                "CP_WORKER_TOKEN": secrets.token_urlsafe(48),
                "CP_BASE_URL": self.public,
                "CP_ENV": "dev",
                "CP_DB_PATH": str(directory / "cp.sqlite"),
            }
        )

    def __enter__(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / "synthetic.pdf").write_bytes(
                b"%PDF-1.4\nSynthetic fixture resume\n%%EOF"
            )
            self.fixture = make_server(8780, self.directory / "fixture")
            self.fixture_thread = threading.Thread(
                target=self.fixture.serve_forever, daemon=True
            )
            self.fixture_thread.start()
            self.app = create_app(self.config)
            self.uvicorn = uvicorn.Server(
                uvicorn.Config(
                    self.app,
                    host="127.0.0.1",
                    port=self.cp_port,
                    log_level="error",
                    access_log=False,
                    timeout_graceful_shutdown=2,
                )
            )
            self.cp_thread = threading.Thread(target=self.uvicorn.run, daemon=True)
            self.cp_thread.start()
            wait_for(
                lambda: (
                    self.client.get(
                        "/api/worker/runs/g3/commands",
                        headers={"Authorization": "Bearer " + self.config.worker_token},
                    ).status_code
                    == 200
                ),
                "control plane startup",
            )
            chrome_path = (
                os.environ.get("CHROME_PATH")
                or shutil.which("chrome")
                or r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
            )
            self.cdp = f"http://127.0.0.1:{free_port()}"
            args = [
                chrome_path,
                f"--remote-debugging-port={urlsplit(self.cdp).port}",
                f"--user-data-dir={self.directory / 'chrome'}",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            if not self.headed:
                args.append("--headless=new")
            args.append("about:blank")
            self.chrome = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            wait_for(
                lambda: httpx.get(self.cdp + "/json/version").status_code == 200,
                "Chrome CDP startup",
            )
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        for process in self.children + ([self.chrome] if self.chrome else []):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        self.client.close()
        if self.uvicorn:
            self.uvicorn.should_exit = True
            self.cp_thread.join(timeout=10)
            self.app.state.store.close()
        if self.fixture:
            self.fixture.shutdown()
            self.fixture.server_close()
            self.fixture_thread.join(timeout=5)

    def worker(self, mode: str = "tick", crash: str | None = None) -> None:
        """Spawn a fresh worker, or kill it at an observed pre-click barrier."""
        env = dict(
            os.environ,
            G3_CP_LOCAL=self.base,
            G3_CP_PUBLIC=self.public,
            G3_WORKER_TOKEN=self.config.worker_token,
            G3_SIGNING_KEY=self.config.signing_key.decode(),
            G3_CDP=self.cdp,
        )
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            mode,
            "--state-dir",
            str(self.directory),
        ]
        if crash:
            (self.directory / "crash-ready").unlink(missing_ok=True)
            command += ["--crash", crash]
        process = subprocess.Popen(
            command,
            env=env,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.children.append(process)
        if crash:
            wait_for(
                lambda: (
                    (self.directory / "crash-ready").exists()
                    or process.poll() is not None
                ),
                "worker crash barrier",
                60,
            )
            if process.poll() is not None:
                raise RuntimeError(
                    "worker exited before crash barrier: "
                    + process.stderr.read().decode()[-3000:]
                )
            process.kill()
            process.wait(timeout=10)
        else:
            _, error = process.communicate(timeout=90)
            if process.returncode:
                raise RuntimeError("worker failed: " + error.decode()[-5000:])

    @property
    def state(self) -> dict:
        return json.loads((self.directory / "state.json").read_text(encoding="utf-8"))

    @property
    def job(self) -> dict:
        return self.state["jobs"]["fixture"]

    @property
    def events(self) -> list[dict]:
        return [
            json.loads(line)
            for line in (self.directory / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]

    @property
    def link(self) -> str:
        return next(
            event["links"]["review"]
            for event in reversed(self.events)
            if "review" in event["links"]
        )

    def fingerprint(self) -> str:
        """Compare all logical SQLite contents, including command/token tables."""
        with sqlite3.connect(self.config.db_path) as db:
            return hashlib.sha256("\n".join(db.iterdump()).encode()).hexdigest()

    def browser_review(self, *, keep_open: bool = False) -> str:
        """Open the captured review capability in real Chrome using a separate tab."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.cdp)
            page = browser.contexts[0].new_page()
            try:
                response = page.goto(self.link.replace(self.public, self.base, 1))
                assert response.status == 200
                html = page.content()
                page.screenshot(
                    path=str(self.directory / "review-page.png"), full_page=True
                )
                return html
            finally:
                if not keep_open:
                    page.close()
                browser.close()

    def page(self, *, run: bool = False) -> str:
        link = (
            self.link
            if not run
            else self.public + "/r/g3?t=" + self.app.state.tokens.mint("view", "g3")
        )
        response = self.client.get(link.replace(self.public, self.base, 1))
        assert response.status_code == 200, "review GET failed"
        return response.text

    def form(self, action: str, *, field: str | None = None, run: bool = False) -> dict:
        forms = [
            form["fields"]
            for form in Forms(self.page(run=run)).forms
            if form["action"] == "/api/" + action
            and (field is None or form["fields"].get("field_key") == field)
        ]
        assert forms, "required review form unavailable: " + action
        return forms[0]

    def post(self, action: str, fields: dict, expected: int = 200):
        response = self.client.post(
            "/api/" + action,
            json=fields,
            headers={"Origin": self.origin, "Sec-Fetch-Site": "same-origin"},
        )
        assert response.status_code == expected, (
            f"{action}: {response.status_code} {response.text}"
        )
        return response

    def counter(self) -> dict:
        return self.fixture.RequestHandlerClass.store.snapshot()

    def reach_review(self) -> None:
        """Use only CP-issued commands for synthetic human answer/handoff gates."""
        self.worker("start")
        for _ in range(10):
            if self.job["status"] == "READY_FOR_REVIEW":
                break
            forms = Forms(self.page_for_gate()).forms
            answers = [
                form["fields"] for form in forms if form["action"] == "/api/answer"
            ]
            if answers:
                self.post("answer", dict(answers[0], value=True))
                self.worker()
            else:
                done = next(
                    form["fields"]
                    for form in forms
                    if form["action"] == "/api/handoff_done"
                )
                self.post("handoff_done", done)
                self.worker("human")
        assert self.job["status"] == "READY_FOR_REVIEW"
        assert self.counter()["total"] == 0

    def page_for_gate(self) -> str:
        token = self.app.state.tokens.mint("view", "g3", job="fixture")
        response = self.client.get("/r/g3/fixture", params={"t": token})
        assert response.status_code == 200
        return response.text


def exercise(
    scenario: Scenario, *, edit: bool = False, crash: str | None = None, timeline=print
) -> dict:
    """Verify review, pause/resume, token security and one fixture-only submit outcome."""
    scenario.reach_review()
    timeline("1. Filled ATS A; review gate paused; fixture submissions = 0")
    before = scenario.fingerprint()
    html = scenario.browser_review()
    assert "synthetic@example.test" in html and "Read-back fields" in html
    assert scenario.fingerprint() == before and scenario.counter()["total"] == 0
    assert scenario.client.get("/events/g3").status_code == 401
    view_token = scenario.app.state.tokens.mint("view", "g3", job="fixture")
    with scenario.client.stream(
        "GET", "/events/g3", headers={"Authorization": "Bearer " + view_token}
    ) as stream:
        assert stream.status_code == 200
        next(stream.iter_lines())
    assert scenario.fingerprint() == before
    timeline(
        "2. Review GET displayed read-back without changing any CP table; SSE without view token = 401"
    )
    scenario.post("pause", scenario.form("pause", run=True))
    operations = (scenario.directory / "operations.txt").read_text()
    scenario.worker()
    assert scenario.job["paused"]
    review = scenario.job["review_snapshot_hash"]
    scenario.worker()
    assert scenario.job["paused"] and scenario.job["review_snapshot_hash"] == review
    assert scenario.counter()["total"] == 0
    assert (scenario.directory / "operations.txt").read_text() == operations
    scenario.post("resume", scenario.form("resume", run=True))
    scenario.worker()
    assert not scenario.job["paused"]
    assert scenario.job["status"] == "READY_FOR_REVIEW", (
        "resume must return to a live review gate"
    )
    timeline(
        "3. Pause survived a fresh worker with no submit; resume returned to review"
    )
    if edit:
        old = scenario.form("approve")
        old_values = {
            field["field_key"]: field["actual"]
            for field in scenario.job["review_snapshot"]["fields"]
        }
        edit_operations = (
            (scenario.directory / "operations.txt").read_text().splitlines()
        )
        email = next(
            field["key"] for field in scenario.job["fields"] if field["type"] == "email"
        )
        scenario.post(
            "edit",
            dict(
                scenario.form("edit", field=email),
                value="edited-synthetic@example.test",
            ),
        )
        scenario.worker()
        assert scenario.job["review_snapshot_hash"] != review
        new_values = {
            field["field_key"]: field["actual"]
            for field in scenario.job["review_snapshot"]["fields"]
        }
        assert new_values == dict(
            old_values, **{email: "edited-synthetic@example.test"}
        )
        assert (
            scenario.directory / "operations.txt"
        ).read_text().splitlines() == edit_operations + ["execute"]
        assert scenario.post("approve", old, 409).json()["error"] == "stale_snapshot"
        assert scenario.counter()["total"] == 0
        assert "edited-synthetic@example.test" in scenario.page()
        timeline(
            "4. Edited email via POST; new hash; old snapshot approval rejected = 409"
        )
    approval = scenario.form("approve")
    planning = (scenario.directory / "planning.txt").read_text()
    scenario.post("approve", approval)
    assert scenario.post("approve", approval, 409).json()["error"] == "token_replayed"
    timeline(
        "5. Approved with page-issued act token and browser Origin; replay rejected = 409"
    )
    if crash:
        scenario.worker(crash=crash)
        assert scenario.counter()["total"] == 0
        expected = "APPROVED" if crash == "before_claim" else "SUBMITTING"
        assert (
            SQLiteLedger(scenario.directory / "ledger.sqlite")
            .get_status("g3", "fixture")
            .value
            == expected
        )
        assert httpx.get(scenario.cdp + "/json/version").status_code == 200
        timeline(
            "6. Killed worker after approval at " + crash + "; Chrome remains alive"
        )
    scenario.worker()
    scenario.worker()
    total = scenario.counter()["total"]
    status = scenario.job["status"]
    assert (scenario.directory / "planning.txt").read_text() == planning
    if crash == "after_claim":
        assert total == 0 and status == "SUBMITTED_UNVERIFIED"
        assert any(event["event_id"] == "E11" for event in scenario.events)
    else:
        assert total == 1 and status == "SUBMITTED_VERIFIED"
        assert any(event["event_id"] == "E10" for event in scenario.events)
        expected_email = (
            "edited-synthetic@example.test" if edit else "synthetic@example.test"
        )
        assert scenario.counter()["items"][0]["fields"]["email"] == expected_email
        assert scenario.job["review_snapshot"]["screenshots"]
        assert scenario.job["evidence"] and all(
            Path(path).is_file() for path in scenario.job["evidence"]
        )
    clicks = (
        (scenario.directory / "operations.txt").read_text().splitlines().count("submit")
    )
    assert clicks == total
    timeline(
        f"7. Fresh worker and replay: {status}; fixture submissions = {total}; no second click"
    )
    return {"status": status, "submissions": total}


def present_review(scenario: Scenario, *, telegram: bool, pause: bool) -> None:
    """Deliver the real review message, or print only the requested view link."""
    bot, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if bot and chat and telegram:
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{bot}/sendMessage",
                json={
                    "chat_id": chat,
                    "text": "G3 synthetic fixture ready for review; no application submitted.",
                    "disable_web_page_preview": True,
                    "reply_markup": {
                        "inline_keyboard": [
                            [{"text": "Review fixture", "url": scenario.link}]
                        ]
                    },
                },
                timeout=15,
            )
            ok = response.status_code == 200 and response.json().get("ok")
        except (httpx.HTTPError, ValueError):
            ok = False
        if not ok:
            raise RuntimeError(
                "Telegram delivery failed; credentials and response suppressed"
            )
        print("Telegram review delivered with URL button and preview disabled")
    else:
        print("Review link (temporary view capability): " + scenario.link)
    if pause:
        input(
            "Open the review link for the video; press Enter to run fixture approval/edit steps: "
        )


def main() -> None:
    """Headed fixture demo with optional explicitly authorised Telegram review delivery."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="run the headed rehearsal without a keyboard pause",
    )
    parser.add_argument(
        "--cp-port",
        type=int,
        default=8790,
        help="local tunnel target port (default 8790)",
    )
    parser.add_argument("--child", choices=["start", "tick", "human"])
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--crash", choices=["before_claim", "after_claim"])
    args = parser.parse_args()
    if args.child:
        child_worker(args.state_dir, args.child, args.crash)
        return
    from dotenv import load_dotenv

    load_dotenv(os.environ.get("ENV_FILE", r"C:\Balaastra\hulchul-operator\.env"))
    with (
        tempfile.TemporaryDirectory(prefix="hulchul-g3-") as temporary,
        Scenario(
            Path(temporary),
            headed=not args.headless,
            public_url=os.getenv("CP_BASE_URL"),
            cp_port=args.cp_port,
        ) as scenario,
    ):
        original = scenario.reach_review

        def review():
            original()
            if not args.headless:
                scenario.browser_review(keep_open=True)
            present_review(
                scenario,
                telegram=not args.no_telegram,
                pause=not args.headless and not args.auto,
            )

        scenario.reach_review = review
        result = exercise(scenario, edit=True, crash="before_claim")
        print("G3 PASS: " + json.dumps(result))


if __name__ == "__main__":
    main()
