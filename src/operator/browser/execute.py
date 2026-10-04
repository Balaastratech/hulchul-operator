"""Action executor for filling form fields, selects, radios, comboboxes, and uploads."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from playwright.sync_api import Locator, Page

from src.operator.browser.evidence import EvidenceManager
from src.operator.browser.models import ActionResult, FieldSpec, FillAction

logger = logging.getLogger(__name__)


class ActionExecutor:
    """Executes validated FillActions on a Playwright Page."""

    def __init__(self, page: Page, evidence_manager: EvidenceManager | None = None) -> None:
        self.page = page
        self.evidence = evidence_manager

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
            logger.warning("Action execution failed on %s: %s", action.field_key, e)
            if self.evidence:
                shot = self.evidence.capture_screenshot_sync(self.page, f"error_{action.field_key}")
                if shot:
                    evidence_files.append(shot)
            return ActionResult(
                field_key=action.field_key,
                success=False,
                reason=f"Action execution error: {e}",
                evidence=evidence_files,
            )
