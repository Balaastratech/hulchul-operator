"""Owned integration glue for the browser builder's synchronous components."""

import asyncio
import hashlib
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

from src.operator.contracts import (
    ActionResult,
    FieldResult,
    FieldSpec,
    FillAction,
    FillReport,
    JobState,
    PageState,
    ReviewSnapshot,
    SubmissionResult,
    Upload,
)
from src.operator.policy.allowlist import DomainAllowlist


class BrowserBridge:
    """Async Port facade; all sync Playwright objects live on one dedicated thread."""

    def __init__(
        self, manager: Any, allowlist: DomainAllowlist, evidence_dir: Path | None = None
    ) -> None:
        """Receive the peer-owned CDP manager without modifying its module."""
        self.manager = manager
        self.allowlist = allowlist
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.fields: dict[str, Any] = {}
        self.actions: dict[str, FillAction] = {}
        self.uploads: dict[str, Upload] = {}
        self.page = None
        self.evidence_dir = evidence_dir or Path("runs/evidence")
        self.evidence_index = 0
        self.live_uploads = {}
        self.last_navigation = time.monotonic()

    async def restore(self, job: JobState) -> None:
        """Restore locator identities and intended flags, never cached live values."""
        from src.operator.browser.models import FieldSpec as PeerField

        self.fields = {
            item.key: PeerField.model_validate(item.model_dump()) for item in job.fields
        }
        self.actions = {item.field_key: item for item in job.actions}
        self.uploads = (
            {item.field_key: item for item in job.review_snapshot.uploads}
            if job.review_snapshot
            else {}
        )

    async def _call(self, function, *args):
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, partial(function, *args)
        )

    def _attach(self, endpoint: str, target_id: str | None):
        from playwright.sync_api import sync_playwright

        # Avoid manager.connect's auto-launch fallback: restart must only reattach.
        if self.manager._pw is None:
            self.manager._pw = sync_playwright().start()
        self.manager._browser = self.manager._pw.chromium.connect_over_cdp(endpoint)
        contexts = self.manager._browser.contexts
        if not contexts or not contexts[0].pages:
            raise RuntimeError("persistent browser target is missing")
        pages = contexts[0].pages
        if target_id:
            candidates = []
            for page in pages:
                session = contexts[0].new_cdp_session(page)
                try:
                    if (
                        session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
                        == target_id
                    ):
                        candidates.append(page)
                finally:
                    session.detach()
            if len(candidates) != 1:
                raise RuntimeError("saved browser target is missing or ambiguous")
            self.page = candidates[0]
        elif len(pages) == 1:
            self.page = pages[0]
        else:
            raise RuntimeError("multiple browser targets require a saved target ID")
        self.manager._page = self.page
        self.manager._context = contexts[0]
        self.page.route("**/*", self._route)

    def _route(self, route):
        request = route.request
        if request.is_navigation_request() and not self.allowlist.permits(request.url):
            route.abort()
        else:
            route.continue_()

    async def attach(self, endpoint: str, target_id: str | None = None) -> None:
        """Reattach a saved target; missing Chrome never launches another one."""
        await self._call(self._attach, endpoint, target_id)

    async def target_id(self) -> str:
        """Expose serializable CDP target identity to the worker factory."""

        def read():
            session = self.manager._context.new_cdp_session(self.page)
            try:
                return session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
            finally:
                session.detach()

        return await self._call(read)

    async def navigate(self, url: str) -> None:
        """Enforce navigation authority before every redirect request."""
        if not self.allowlist.permits(url):
            raise PermissionError("off-allowlist navigation")

        def navigate():
            self._navigation_pause()
            self.page.goto(url)

        await self._call(navigate)

    def _navigation_pause(self) -> None:
        """Keep at least five seconds plus jitter between navigation attempts."""
        remaining = (
            5
            + secrets.randbelow(1001) / 1000
            - (time.monotonic() - self.last_navigation)
        )
        if remaining > 0:
            time.sleep(remaining)
        self.last_navigation = time.monotonic()

    async def classify_page(self) -> PageState:
        """Peer deterministic classifier; no model can authorize a submit."""
        from src.operator.browser.classify import PageStateClassifier

        result = await self._call(PageStateClassifier().classify_page, self.page)
        return PageState(result.value)

    def _extract(self):
        from src.operator.browser.extract import FieldExtractor

        fields = FieldExtractor().extract_fields(self.page)
        self.fields.update({item.key: item for item in fields})
        return [
            FieldSpec.model_validate(
                item.model_dump(exclude={"selector", "is_combobox"})
            )
            for item in fields
        ]

    async def extract_fields(self) -> list[FieldSpec]:
        """Project-private locator metadata stays out of frozen public contracts."""
        return await self._call(self._extract)

    def _execute(self, action: FillAction):
        from src.operator.browser.execute import ActionExecutor
        from src.operator.browser.models import FillAction as PeerAction

        field = self.fields[action.field_key]
        if action.action == "check" and action.value is False:
            if field.type != "checkbox":
                raise PermissionError("only a checkbox can be explicitly unchecked")
            locator = ActionExecutor(self.page).find_locator_for_field(field)
            locator.uncheck(force=True)
            self.actions[action.field_key] = action
            return ActionResult(field_key=action.field_key, success=True, actual=False)
        result = ActionExecutor(self.page).execute_action(
            PeerAction.model_validate(action.model_dump()), field
        )
        self.actions[action.field_key] = action
        if result.success and action.action == "upload_resume":
            path = Path(str(action.value))
            self.uploads[action.field_key] = Upload(
                field_key=action.field_key,
                name=path.name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        return ActionResult.model_validate(result.model_dump())

    async def execute(self, action: FillAction) -> ActionResult:
        """Delegate one already-authorized input to the peer executor."""
        return await self._call(self._execute, action)

    def _read(self):
        from src.operator.browser.execute import ActionExecutor

        self._extract()
        values = {}
        self.live_uploads = {}
        executor = ActionExecutor(self.page)
        for key, item in self.fields.items():
            locator = executor.find_locator_for_field(item)
            if locator.count() != 1:
                raise ValueError("read-back locator missing or ambiguous")
            values[key] = locator.evaluate("""el => {
                if (el.type === 'checkbox' || el.type === 'radio') return el.checked;
                if (el.type === 'file') return Array.from(el.files || []).map(f => f.name);
                if (el.tagName === 'SELECT') return Array.from(el.selectedOptions).map(o => o.text.trim()).join(', ');
                return el.value === undefined ? (el.getAttribute('aria-checked') || el.innerText) : el.value;
            }""")
            if item.type == "file":
                files = locator.evaluate("""async el => Promise.all(Array.from(el.files || []).map(async f => ({
                    name:f.name, sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
                    await f.arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('')
                })))""")
                if len(files) > 1:
                    raise ValueError("multiple files require human review")
                if files:
                    self.live_uploads[key] = Upload(field_key=key, **files[0])
        return values

    async def verify(self, actions: list[FillAction]) -> FillReport:
        """Strict read-back prevents fuzzy substring matches from hiding wrong facts."""
        values = await self._call(self._read)
        fields = []
        for action in actions:
            actual = values.get(action.field_key)
            matched = actual == action.value
            if action.action == "upload_resume":
                matched = actual == [Path(str(action.value)).name]
            fields.append(
                FieldResult(
                    field_key=action.field_key,
                    intended=action.value,
                    actual=actual,
                    matched=matched,
                    generated=action.generated,
                )
            )
        return FillReport(fields=fields)

    async def click_next(self) -> bool:
        """Peer vocabulary guard prevents navigation from clicking Submit/Apply."""
        from src.operator.browser.navigate import StepNavigator

        def advance():
            navigator = StepNavigator(self.page)
            if navigator.find_next_button() is None:
                return False
            self._navigation_pause()
            return navigator.click_next()

        return await self._call(advance)

    async def capture_evidence(self) -> list[str]:
        """Store synthetic local screenshots; no upload or sensitive filename."""

        def capture():
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            self.evidence_index += 1
            path = self.evidence_dir / f"evidence-{time.time_ns()}.png"
            self.page.screenshot(path=str(path))
            return [str(path.resolve())]

        return await self._call(capture)

    async def read_review(self) -> ReviewSnapshot:
        """Bind live fields and actual upload names; never hash cached browser values."""
        values = await self._call(self._read)
        uploads = []
        for key, upload in self.uploads.items():
            if values.get(key) != [upload.name] or self.live_uploads.get(key) != upload:
                raise ValueError("reviewed upload changed")
            uploads.append(upload)
        return ReviewSnapshot(
            screenshots=await self.capture_evidence(),
            fields=[
                FieldResult(
                    field_key=key,
                    actual=value,
                    matched=(
                        key in self.actions
                        and (
                            value == self.actions[key].value
                            or (
                                self.actions[key].action == "upload_resume"
                                and value == [Path(str(self.actions[key].value)).name]
                            )
                        )
                    ),
                    intended=self.actions[key].value if key in self.actions else None,
                    generated=self.actions[key].generated
                    if key in self.actions
                    else False,
                )
                for key, value in values.items()
            ],
            uploads=uploads,
            generated_texts={
                key: str(action.value)
                for key, action in self.actions.items()
                if action.generated
            },
        )

    async def submission_urls(self) -> list[str]:
        """Read actual browser URL and form/button action targets before any click."""

        def read():
            return [self.page.url] + self.page.evaluate("""() => Array.from(document.forms).flatMap(f =>
                [f.action, ...Array.from(f.querySelectorAll('[formaction]')).map(b => b.formAction)]
            ).filter(Boolean)""")

        return await self._call(read)

    async def submit(self) -> None:
        """Fixture-only final guard in addition to the core ledger claim."""
        urls = await self.submission_urls()
        if any(not self.allowlist.permits_submission(url) for url in urls):
            raise PermissionError("non-fixture submission target")
        await self._call(
            self.page.get_by_role(
                "button",
                name=re.compile(r"^(submit|apply)( application)?$", re.IGNORECASE),
            ).click
        )

    async def verify_submission(self) -> SubmissionResult:
        """Deterministic confirmation signal and visible text; no further click."""
        state = await self.classify_page()
        text = await self._call(self.page.locator("body").inner_text)
        return SubmissionResult(
            verified=state == PageState.CONFIRMATION,
            confirmation=text[:1000] if state == PageState.CONFIRMATION else None,
        )

    async def disconnect(self) -> None:
        """Drop the Playwright connection; persistent Chrome remains outside worker."""
        await self._call(self.manager.disconnect)
        self.executor.shutdown(wait=True)
