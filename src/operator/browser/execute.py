"""Action executor for filling form fields, selects, radios, comboboxes, and uploads."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from pydantic import BaseModel
from playwright.sync_api import Locator, Page

from src.operator.browser.evidence import EvidenceManager
from src.operator.browser.models import ActionResult, FieldSpec, FillAction

logger = logging.getLogger(__name__)


class ActionExecutor:
    """Executes validated FillActions on a Playwright Page."""

    def __init__(
        self,
        page: Page,
        evidence_manager: EvidenceManager | None = None,
        llm: Any | None = None,
    ) -> None:
        self.page = page
        self.evidence = evidence_manager
        self.llm = llm

    def find_locator_for_field(self, field: FieldSpec) -> Locator:
        """Locate element on page by opid, selector, or accessible attributes."""
        # 1. By explicit data-opid if set during extraction
        if field.id and field.id.isdigit():
            loc = self.page.locator(f'[data-opid="{field.id}"]').first
            if loc.count() > 0:
                return loc

        # 2. By selector if provided
        if field.selector:
            loc = self.page.locator(field.selector).first
            if loc.count() > 0:
                return loc

        # 3. By label or name
        if field.label:
            try:
                loc = self.page.get_by_label(field.label, exact=False).first
                if loc.count() > 0:
                    return loc
            except Exception:
                pass

        # 4. Fallback: by placeholder
        try:
            loc = self.page.get_by_placeholder(field.label, exact=False).first
            if loc.count() > 0:
                return loc
        except Exception:
            pass

        return self.page.locator(f'[name="{field.label}"]').first

    def execute_action(
        self,
        action: FillAction,
        field: FieldSpec,
    ) -> ActionResult:
        """Execute one fill action, capture evidence, and return structured ActionResult."""
        act = action.action
        val = action.value
        evidence_files: list[str] = []

        if act == "skip":
            return ActionResult(
                field_key=action.field_key,
                success=True,
                actual=field.current_value,
                reason="Skipped as requested",
            )

        if act == "ask_user":
            return ActionResult(
                field_key=action.field_key,
                success=False,
                actual=field.current_value,
                reason=f"Escalated to human: {action.question}",
            )

        loc = self.find_locator_for_field(field)
        if loc.count() == 0:
            if self.evidence:
                shot = self.evidence.capture_screenshot_sync(self.page, f"fail_find_{action.field_key}")
                if shot:
                    evidence_files.append(shot)
            return ActionResult(
                field_key=action.field_key,
                success=False,
                reason=f"Element locator not found for {action.field_key} (label: '{field.label}')",
                evidence=evidence_files,
            )

        try:
            # 1. File Upload (Resume)
            if act == "upload_resume":
                file_path = str(val) if val else ""
                if not file_path or not Path(file_path).exists():
                    return ActionResult(
                        field_key=action.field_key,
                        success=False,
                        reason=f"Resume file path not found: {file_path}",
                    )
                loc.set_input_files(file_path, timeout=5000)
                if self.evidence:
                    shot = self.evidence.capture_screenshot_sync(self.page, f"upload_{action.field_key}")
                    if shot:
                        evidence_files.append(shot)
                return ActionResult(
                    field_key=action.field_key,
                    success=True,
                    actual=Path(file_path).name,
                    evidence=evidence_files,
                )

            # 2. Checkbox or Radio button
            if act == "check":
                # Handle false checkbox explicitly (uncheck)
                is_false_check = (val is False or str(val).lower() in ("false", "0", "no", "unchecked", "off"))
                try:
                    if is_false_check:
                        loc.uncheck(force=True, timeout=4000)
                    else:
                        loc.check(force=True, timeout=4000)
                except Exception:
                    # Check current checked state if clicking label
                    currently_checked = False
                    try:
                        currently_checked = loc.is_checked()
                    except Exception:
                        pass
                    # If target matches current state, do nothing
                    should_be_checked = not is_false_check
                    if currently_checked != should_be_checked:
                        parent_label = loc.locator("xpath=ancestor::label[1]")
                        if parent_label.count() > 0:
                            parent_label.first.click(force=True, timeout=3000)
                        else:
                            loc.click(force=True, timeout=3000)

                actual_val = "false" if is_false_check else "true"
                if self.evidence:
                    shot = self.evidence.capture_screenshot_sync(self.page, f"check_{action.field_key}")
                    if shot:
                        evidence_files.append(shot)
                return ActionResult(
                    field_key=action.field_key,
                    success=True,
                    actual=actual_val,
                    evidence=evidence_files,
                )

            # 3. Yes/No button widget
            if field.type == "yes_no_button" or (field.group == "Button Group" and act in ("select", "fill")):
                target_val = str(val or "").strip()
                # Find matching button within the group
                btn = loc.locator(f"button:has-text('{target_val}')").first
                if btn.count() == 0:
                    # Try case-insensitive text match
                    btn = loc.locator("button").filter(has_text=re.compile(f"^{re.escape(target_val)}$", re.I)).first
                if btn.count() > 0:
                    btn.click(timeout=3000)
                    actual_val = target_val
                else:
                    return ActionResult(
                        field_key=action.field_key,
                        success=False,
                        reason=f"Button option '{target_val}' not found in yes/no widget",
                        evidence=evidence_files,
                    )
                if self.evidence:
                    shot = self.evidence.capture_screenshot_sync(self.page, f"yesno_{action.field_key}")
                    if shot:
                        evidence_files.append(shot)
                return ActionResult(
                    field_key=action.field_key,
                    success=True,
                    actual=actual_val,
                    evidence=evidence_files,
                )

            # 4. Native Select Dropdown
            if field.type == "select" or act == "select":
                target_val = str(val or "")
                try:
                    loc.select_option(label=target_val, timeout=3000)
                except Exception:
                    # Fuzzy match option label
                    matched_opt = None
                    for opt in field.options:
                        if target_val.lower() in opt.lower() or opt.lower() in target_val.lower():
                            matched_opt = opt
                            break
                    if matched_opt:
                        loc.select_option(label=matched_opt, timeout=3000)
                    else:
                        loc.select_option(value=target_val, timeout=3000)

                actual_val = loc.input_value() if hasattr(loc, "input_value") else target_val
                if self.evidence:
                    shot = self.evidence.capture_screenshot_sync(self.page, f"select_{action.field_key}")
                    if shot:
                        evidence_files.append(shot)
                return ActionResult(
                    field_key=action.field_key,
                    success=True,
                    actual=actual_val,
                    evidence=evidence_files,
                )

            # 5. Combobox (custom autocomplete / listbox)
            if field.is_combobox:
                target_val = str(val or "")
                loc.click(timeout=4000)
                loc.fill(target_val, timeout=4000)
                self.page.wait_for_timeout(600)
                # Look for matching role=option or list item
                option_loc = self.page.locator('[role=option]').filter(has_text=target_val).first
                if option_loc.count() > 0 and option_loc.is_visible():
                    option_loc.click(timeout=3000)
                else:
                    # AUDIT-002: Never press global Enter as fallback, which can submit forms!
                    # Check if an option with role="option" or li exists in an active popup/listbox
                    list_option = self.page.locator('li, [role=option]').filter(has_text=target_val).first
                    if list_option.count() > 0 and list_option.is_visible():
                        list_option.click(timeout=3000)
                    else:
                        # Safe blur/tab without Enter to prevent accidental form submission
                        self.page.keyboard.press("Tab")
                
                if self.evidence:
                    shot = self.evidence.capture_screenshot_sync(self.page, f"combo_{action.field_key}")
                    if shot:
                        evidence_files.append(shot)
                return ActionResult(
                    field_key=action.field_key,
                    success=True,
                    actual=target_val,
                    evidence=evidence_files,
                )

            # 5. Standard Text Input / Textarea
            target_val = str(val or "")
            loc.fill(target_val, timeout=5000)
            actual_val = loc.input_value()
            if self.evidence:
                shot = self.evidence.capture_screenshot_sync(self.page, f"fill_{action.field_key}")
                if shot:
                    evidence_files.append(shot)
            return ActionResult(
                field_key=action.field_key,
                success=True,
                actual=actual_val,
                evidence=evidence_files,
            )

        except Exception as e:
            error_str = str(e)
            logger.warning("Action execution failed on %s: %s", action.field_key, error_str)

            # Error-driven repair: send field description + error text back to LLM ONCE
            repaired, actual_val, new_val = self._attempt_llm_repair(action, field, error_str, loc)
            if repaired:
                action.value = new_val
                action.derived = True
                if self.evidence:
                    shot = self.evidence.capture_screenshot_sync(self.page, f"repair_fill_{action.field_key}")
                    if shot:
                        evidence_files.append(shot)
                return ActionResult(
                    field_key=action.field_key,
                    success=True,
                    actual=actual_val,
                    evidence=evidence_files,
                )

            # If that also fails, mark that single question as 'needs your answer' with library suggestion shown
            suggestion = str(val or "")
            action.question = f"Needs your answer: {field.label} (suggested: {suggestion})"
            action.action = "ask_user"

            if self.evidence:
                shot = self.evidence.capture_screenshot_sync(self.page, f"error_{action.field_key}")
                if shot:
                    evidence_files.append(shot)
            return ActionResult(
                field_key=action.field_key,
                success=False,
                reason=f"needs your answer: {field.label} (suggested: {suggestion}): {error_str}",
                evidence=evidence_files,
            )

    def _attempt_llm_repair(
        self,
        action: FillAction,
        field: FieldSpec,
        error_text: str,
        loc: Locator,
    ) -> tuple[bool, Any, str | None]:
        """Send field description + browser error text to LLM ONCE to produce a corrected value."""
        if not self.llm:
            return False, None, None

        prompt = (
            f"The browser rejected the value '{action.value}' for field '{field.label}' "
            f"with Playwright error: '{error_text}'.\n"
            f"Field description:\n"
            f"- label: {field.label}\n"
            f"- type: {field.type}\n"
            f"- group: {field.group}\n"
            f"- options: {field.options}\n"
            f"- pattern: {field.pattern}\n"
            f"- min: {field.min}\n"
            f"- max: {field.max}\n"
            f"- maxlength: {field.maxlength}\n"
            f"- placeholder: {field.placeholder}\n"
            f"Source fact: {action.source}\n\n"
            f"Produce a single corrected value formatted specifically for this HTML control "
            f"(e.g. for HTML <input type=date>, format as YYYY-MM-DD such as 2026-11-03). "
            f"Never invent facts; adapt the source fact to the control's format.\n"
            f"Return valid JSON matching: {{\"corrected_value\": <value>, \"derived\": true}}"
        )

        class RepairProposal(BaseModel):
            corrected_value: Any
            derived: bool = True

        corrected_value = None
        try:
            if hasattr(self.llm, "generate_structured"):
                res, _ = self.llm.generate_structured(prompt, RepairProposal)
                corrected_value = res.corrected_value
            elif hasattr(self.llm, "structured"):
                import asyncio
                import inspect
                if inspect.iscoroutinefunction(self.llm.structured):
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            import concurrent.futures
                            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                                res = pool.submit(asyncio.run, self.llm.structured(prompt, RepairProposal)).result(timeout=15)
                        else:
                            res = loop.run_until_complete(self.llm.structured(prompt, RepairProposal))
                    except Exception:
                        res = None
                else:
                    res = self.llm.structured(prompt, RepairProposal)
                if res:
                    corrected_value = getattr(res, "corrected_value", None)
            elif hasattr(self.llm, "generate_text"):
                resp = self.llm.generate_text(prompt)
                import json
                import re
                cleaned = re.sub(r"^```(?:json)?\s*", "", resp.content.strip())
                cleaned = re.sub(r"\s*```$", "", cleaned)
                data = json.loads(cleaned)
                corrected_value = data.get("corrected_value")
        except Exception as repair_err:
            logger.warning("LLM repair call failed for %s: %s", action.field_key, repair_err)
            return False, None, None

        if corrected_value is None:
            return False, None, None

        try:
            val_str = str(corrected_value)
            loc.fill(val_str, timeout=5000)
            actual_val = loc.input_value() if hasattr(loc, "input_value") else val_str
            return True, actual_val, val_str
        except Exception as retry_err:
            logger.warning("Repaired fill attempt failed on %s: %s", action.field_key, retry_err)
            return False, None, None
