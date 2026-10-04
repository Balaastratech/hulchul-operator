"""Code enforces source facts, field restrictions and fixture-only submission."""

import re
from dataclasses import dataclass

from src.operator.contracts import (
    AnswerLibrary,
    FieldSpec,
    FillAction,
    Goal,
    Profile,
    Rules,
)

from .allowlist import DomainAllowlist

LEGAL = re.compile(
    r"\b(certif\w*|attest\w*|consent\w*|agree\w*|accept terms|acknowledg\w*|signature)\b",
    re.IGNORECASE,
)
EEO = re.compile(
    r"\b(gender|race|racial|ethnic\w*|orientation|disability|disabled|veteran|demographic|sex)\b",
    re.IGNORECASE,
)
CHALLENGE = re.compile(
    r"captcha|recaptcha|hcaptcha|password|one.time.password|2fa|verification.code",
    re.IGNORECASE,
)
ESSAY = re.compile(
    r"why (us|this|do you)|cover letter|motivation|tell us|describe|essay",
    re.IGNORECASE,
)
SALARY = re.compile(r"salary|compensation|pay expectation", re.IGNORECASE)


@dataclass(frozen=True)
class PolicyDecision:
    """Authorized action or concrete escalation; never silently drop a field."""

    allowed: bool
    action: FillAction
    reason: str | None = None


def _ask(action: FillAction, reason: str) -> PolicyDecision:
    return PolicyDecision(
        False,
        FillAction(field_key=action.field_key, action="ask_user", question=reason),
        reason,
    )


def check_fill(
    action: FillAction,
    field: FieldSpec,
    *,
    url: str,
    allowlist: DomainAllowlist,
    goal: Goal,
    profile: Profile,
    rules: Rules,
    answers: AnswerLibrary,
    resume_path: str,
) -> PolicyDecision:
    """Validate each model proposal against trusted sources and deterministic rules."""
    label = f"{field.label} {field.group} {field.type}"
    if action.field_key != field.key:
        return _ask(action, "Unknown field identity")
    if action.action in {"ask_user", "skip"}:
        return PolicyDecision(True, action)
    from .injection import InjectionClassifier
    injection_check = InjectionClassifier().check_deterministic(field.label)
    if injection_check and injection_check.quarantined:
        return _ask(action, f"Hostile prompt injection detected in field label: {injection_check.reason}")
    if not allowlist.permits(url) or not goal.permits_filling:
        return _ask(action, "Filling is outside the goal or domain authority")
    if CHALLENGE.search(label):
        return _ask(action, "Human login or challenge; never interact automatically")
    if LEGAL.search(label):
        return _ask(action, "Legal acknowledgement requires user action in the browser")
    if EEO.search(label):
        if rules.eeo_policy == "leave_blank":
            return PolicyDecision(True, FillAction(field_key=field.key, action="skip"))
        if rules.eeo_policy == "decline" and isinstance(action.value, str):
            decline = action.value.casefold()
            if decline in {
                "decline",
                "decline to answer",
                "prefer not to say",
                "i don't wish to answer",
                "decline to self-identify",
                "prefer not to answer",
            } and (not field.options or action.value in field.options):
                return PolicyDecision(True, action)
        return _ask(
            action, "Demographic answer requires an explicit rule or human answer"
        )
    if action.action == "upload_resume":
        if not re.search(
            r"\b(resume|cv|curriculum vitae)\b", field.label, re.IGNORECASE
        ):
            return _ask(
                action, "Only a resume upload control is permitted automatically"
            )
        if action.value != resume_path or "resume" not in rules.allowed_uploads:
            return _ask(action, "Only the snapshotted resume is permitted")
        if field.type != "file":
            return _ask(action, "Upload requires a file control")
        return PolicyDecision(True, action)
    if field.type == "file":
        return _ask(action, "File upload action required")
    if action.generated:
        if SALARY.search(label):
            return _ask(
                action,
                "Salary cannot be generated; an explicit Rules value is required",
            )
        if (
            not ESSAY.search(label)
            or action.action != "fill"
            or not isinstance(action.value, str)
        ):
            return _ask(action, "Generated answers are allowed only for essay fields")
        return PolicyDecision(True, action)
    if SALARY.search(label):
        rules_match = rules.salary_expectation and action.value == rules.salary_expectation
        answers_match = False
        source = action.source or ""
        if source.startswith("answers."):
            pattern = source.removeprefix("answers.")
            rows = [
                row
                for row in answers.answers
                if row.pattern == pattern and row.sensitivity not in ("sensitive", "legal")
            ]
            if len(rows) == 1 and (rows[0].answer == action.value or action.derived):
                answers_match = True
        if not (rules_match or answers_match):
            return _ask(
                action, "Salary expectation is not explicitly supplied by Rules or Answer Library"
            )
    else:
        source = action.source or ""
        value = None
        found = False
        if source.startswith("profile."):
            value = profile.model_dump()
            for part in source.split(".")[1:]:
                if isinstance(value, dict) and part in value:
                    value, found = value[part], True
                elif (
                    isinstance(value, list)
                    and part.isdigit()
                    and int(part) < len(value)
                ):
                    value, found = value[int(part)], True
                else:
                    found = False
                    break
        elif source.startswith("answers."):
            pattern = source.removeprefix("answers.")
            rows = [
                row
                for row in answers.answers
                if row.pattern == pattern and row.sensitivity not in ("sensitive", "legal")
            ]
            if len(rows) == 1:
                value, found = rows[0].answer, True
        elif source.startswith("rules."):
            rule_key = source.removeprefix("rules.")
            rules_dict = rules.model_dump()
            if rule_key in rules_dict:
                value, found = rules_dict[rule_key], True
        elif source == "resume":
            value, found = resume_path, True

        if not found or value is None:
            return _ask(action, f"Answer has no matching explicit source fact: {source}")

        if not action.derived:
            matches = value == action.value
            if isinstance(value, bool) and isinstance(action.value, str):
                matches = action.value.casefold() in (
                    {"yes", "true"} if value else {"no", "false"}
                )
            if not matches:
                return _ask(action, "Answer has no matching explicit source fact")
    if field.options and action.action in {"select", "check"}:
        values = action.value if isinstance(action.value, list) else [action.value]
        if any(value not in field.options for value in values):
            return _ask(action, "Proposed option is absent from the extracted control")
    return PolicyDecision(True, action)


def authorize_submit(
    *,
    url: str,
    allowlist: DomainAllowlist,
    goal: Goal,
    approved_hash: str,
    live_hash: str,
) -> None:
    """Preflight only; durable ledger begin_submission remains mandatory."""
    if goal.mode != "normal" or not goal.permits_filling:
        raise PermissionError("goal does not permit submission")
    if not allowlist.permits_submission(url):
        raise PermissionError("real employer submission is forbidden")
    if not approved_hash or approved_hash != live_hash:
        raise PermissionError("review snapshot is stale")
