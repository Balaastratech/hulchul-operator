"""Real Services factory: python -m worker.main --factory src.operator.app.factory:build_services."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote

from dotenv import load_dotenv
from pydantic import BaseModel

from control_plane.config import Config, load_config
from control_plane.tokens import TokenService
from src.operator.app.data import RealData
from src.operator.app.review import SubmissionPolicy
from src.operator.browser.cdp import CDPBrowserManager
from src.operator.channels.web import HttpSink, WebChannel
from src.operator.contracts import (
    AnswerLibrary,
    Event,
    FieldSpec,
    FillAction,
    Goal,
    Profile,
    Rules,
)
from src.operator.data.factory import get_data_source
from src.operator.graph import Services
from src.operator.graph.adapters import BrowserBridge
from src.operator.ledger import SQLiteLedger
from src.operator.llm.factory import get_llm_port
from src.operator.llm.protocol import LLMPort, UsageSummary
from src.operator.policy.allowlist import DomainAllowlist
from src.operator.policy.authority import check_fill
from src.operator.policy.injection import InjectionClassifier

ROOT = Path(__file__).resolve().parents[3]
T = TypeVar("T", bound=BaseModel)


class RealLLM:
    """Explain the frozen source-path vocabulary to the real peer LLM port."""

    def __init__(self, port: LLMPort, allowlist: DomainAllowlist) -> None:
        self.port = port
        self.allowlist = allowlist

    async def structured(self, prompt: str, response_model: type[T]) -> T:
        """Real Gemini proposes; deterministic graph policy still validates all actions."""
        if response_model is Goal:
            prompt += (
                "\nParse the human's intent: 'apply' means mode=normal, with mandatory human approval "
                "before submission. Use dry_run only if the human explicitly requests a dry run, "
                "fill-only, or no submission. 'Under my rules' does not itself mean dry_run."
            )
        if response_model.__name__ == "AnswerPlan":
            prompt += (
                "\nSOURCE RULES: Use only keys present in the supplied profile object. "
                "It has name but NOT first_name, last_name or country: use answers.first_name, "
                "answers.last_name and answers.country when those exact pattern rows are present. "
                "An answers.<pattern> path uses the COMPLETE literal pattern string, including spaces, "
                "pipes and regex characters. Copy the exact answer text; do not paraphrase or shorten. "
                "An optional field without a directly representable source/value must be skip, not a guess. "
                "Checkbox/radio values must be boolean; do not turn a skills string into true. "
                "Skip optional skills/relocation radio questions without a matching boolean source. "
                "Skip optional salary if rules.salary_expectation is null. Ask required legal controls; "
                "the human completes them. File uploads use source=resume. Never upload any other file."
                " For upload_resume, copy the EXACT resume_path string as value, including slash style."
            )
        plan = await self.port.structured(prompt, response_model)
        if response_model.__name__ == "AnswerPlan":
            payload = json.loads(
                prompt.split("<untrusted_data>")[1].split("</untrusted_data>")[0]
            )
            fields = {f["key"]: FieldSpec.model_validate(f) for f in payload["fields"]}
            for index, action in enumerate(plan.actions):
                if (
                    action.action == "upload_resume"
                    and isinstance(action.value, str)
                    and Path(action.value).resolve()
                    == Path(payload["resume_path"]).resolve()
                ):
                    action.value = payload["resume_path"]
                field = fields.get(action.field_key)
                if (
                    field
                    and field.type == "radio"
                    and action.action == "check"
                    and action.value is False
                ):
                    plan.actions[index] = FillAction(
                        field_key=action.field_key, action="skip"
                    )
                    continue
                if field is None or field.required:
                    continue
                checked = check_fill(
                    action,
                    field,
                    url="http://" + min(self.allowlist.hosts),
                    allowlist=self.allowlist,
                    goal=Goal(mode="normal"),
                    profile=Profile.model_validate(payload["profile"]),
                    rules=Rules.model_validate(payload["rules"]),
                    answers=AnswerLibrary.model_validate(payload["answers"]),
                    resume_path=payload["resume_path"],
                )
                if not checked.allowed or action.action == "ask_user":
                    plan.actions[index] = FillAction(
                        field_key=action.field_key, action="skip"
                    )
        return plan

    def get_usage(self) -> UsageSummary:
        """Expose real provider usage for the run report."""
        return self.port.get_usage()


def load_environment() -> None:
    """Read only the canonical ENV_FILE; environment overrides it without copying secrets."""
    load_dotenv(
        os.environ.get("ENV_FILE", r"C:\Balaastra\hulchul-operator\.env"),
        override=False,
    )


class RealChannel:
    """Translate snapshot vocabulary, publish via peer channels, journal a redacted timeline."""

    def __init__(
        self,
        directory: Path,
        config: Config,
        browser: BrowserBridge,
        allowlist: DomainAllowlist,
        tokens: TokenService,
    ) -> None:
        self.directory = directory
        self.browser = browser
        self.allowlist = allowlist
        self.tokens = tokens
        self.local_url = os.environ.get("CP_LOCAL_URL", config.base_url)
        self.public_url = config.base_url
        self.sink = HttpSink(
            os.environ.get("CP_LOCAL_URL", config.base_url),
            "Bearer " + config.worker_token,
        )
        self.web = WebChannel(self.sink)
        self.policy = SubmissionPolicy(
            config.db_path.parent / "submission-policy.sqlite"
        )
        self.telegram = None
        self.bot_api = None
        if config.telegram_enabled and os.environ.get("REAL_NO_TELEGRAM") != "1":
            from src.operator.channels.telegram import BotApi, TelegramChannel

            self.bot_api = BotApi(config.telegram_bot_token)
            self.telegram = TelegramChannel(
                self.bot_api,
                chat_ids=config.telegram_chat_ids,
                public_url=config.base_url,
                tokens=tokens,
            )

    async def emit(self, event: Event) -> None:
        """Persist snapshot first; web/Telegram receive the same immutable review."""
        payload = dict(event.payload)
        if event.event_id in {"E07", "E08"}:
            payload["review_snapshot"] = payload.pop("review")
            urls = await self.browser.submission_urls()
            allowed = bool(urls) and all(
                self.allowlist.permits_submission(url) for url in urls
            )
            self.policy.record(
                event.run_id, event.job_id, payload["snapshot_hash"], allowed
            )
            payload.update(submission_allowed=allowed, job_url=urls[0] if urls else "")
        if event.event_id == "E14" and isinstance(payload.get("jobs"), dict):
            payload["jobs"] = list(payload["jobs"].values())
        adapted = event.model_copy(update={"payload": payload})
        await self.web.emit(adapted)
        if self.telegram:
            await self.telegram.emit(adapted)
        # Do not persist signed view tokens or candidate values to the timeline.
        with (self.directory / "timeline.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "event": event.event_id,
                        "job": event.job_id,
                        "message": event.message,
                        "time": event.created_at.isoformat(),
                    }
                )
                + "\n"
            )
        print(f"{event.event_id} {event.job_id or '-'}: {event.message}", flush=True)
        if event.event_id in {"E07", "E08"}:
            print("Review: " + event.links.get("review", ""), flush=True)
            print(
                "Submission: "
                + (
                    "fixture only"
                    if payload["submission_allowed"]
                    else "disabled (D-014)"
                ),
                flush=True,
            )

    async def close(self) -> None:
        """Release HTTP sessions owned by this composition."""
        await self.sink._client.aclose()
        if self.bot_api:
            await self.bot_api.aclose()


def build_services() -> Services:
    """Wire real Vertex/data/CDP/SQLite/web and optional Telegram from ENV_FILE."""
    load_environment()
    directory = Path(os.environ.get("REAL_STATE_DIR", "runs/real")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if os.environ.get("CHAT_ID") and not os.environ.get("TELEGRAM_CHAT_ID"):
        os.environ["TELEGRAM_CHAT_ID"] = os.environ["CHAT_ID"]
    config = load_config()
    tokens = TokenService(config.signing_key, config.signing_key_previous)
    llm = get_llm_port()
    source_type = os.environ.get("DATA_SOURCE", "local_folder").strip()
    if source_type not in {"local_folder", "drive_public"}:
        raise ValueError("DATA_SOURCE must be local_folder or drive_public")
    local_path = Path(os.environ.get("LOCAL_DATA_DIR", str(ROOT / "sample_data")))
    source = get_data_source(
        source_type,
        folder_or_path=local_path if source_type == "local_folder" else None,
        fallback_dir=local_path,
    )
    if source_type == "drive_public":
        configured = os.environ.get("DRIVE_FILE_IDS_JSON")
        if configured:
            source.file_ids = json.loads(configured)
        else:
            # The known public demo IDs are documented configuration, not candidate values.
            # Only use this map for that exact folder; other folders use the peer discovery port.
            documentation = ROOT / "docs/03-architecture/DATA_SOURCES.md"
            text = documentation.read_text(encoding="utf-8")
            folder = re.search(r"Folder `hulchul-operator-data`: `([^`]+)`", text)
            if folder and folder.group(1) == source.folder_id:
                names = {
                    "profile": "profile.md",
                    "rules": "rules.md",
                    "answers": "answers.csv",
                    "resume": "resume.pdf",
                }
                for name, filename in names.items():
                    row = re.search(
                        r"\| " + name + r" \|[^\n]*?`([A-Za-z0-9_-]+)`", text
                    )
                    if row:
                        source.file_ids[filename] = row.group(1)
    host = os.environ.get("REAL_FIXTURE_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "127.0.0.2"}:
        raise ValueError("REAL_FIXTURE_HOST must be a supported loopback address")
    fixtures = [f"http://{host}:8780"]
    allowlist = DomainAllowlist.from_urls(
        fixtures, fixture_urls=fixtures, control_plane_url=config.base_url
    )
    browser = BrowserBridge(CDPBrowserManager(), allowlist, directory / "evidence")
    channel = RealChannel(directory, config, browser, allowlist, tokens)
    classifier = InjectionClassifier(llm)

    def scan(text: str) -> bool:
        result = classifier.classify(text)
        # The peer classifier currently returns clean on provider error. Composition fails closed.
        return result.flagged or result.confidence == 0

    return Services(
        browser=browser,
        llm=RealLLM(llm, allowlist),
        data=RealData(
            source,
            directory,
            allowlist,
            include_public=os.environ.get("REAL_INCLUDE_PUBLIC") == "1",
        ),
        channel=channel,
        ledger=SQLiteLedger(directory / "ledger.sqlite"),
        allowlist=allowlist,
        injection_scan=scan,
        submission_urls=browser.submission_urls,
        restore_browser=browser.restore,
        target_id=browser.target_id,
        review_url=lambda run, job, digest: (
            f"{config.base_url}/r/{quote(run)}/{quote(job)}?t={quote(tokens.mint('view', run, job=job))}"
        ),
    )
