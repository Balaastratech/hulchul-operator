"""Browser operator package exporting CDP, execute, evidence, and models."""

from src.operator.browser.cdp import CDPBrowserManager
from src.operator.browser.classify import PageStateClassifier
from src.operator.browser.evidence import EvidenceManager
from src.operator.browser.execute import ActionExecutor
from src.operator.browser.models import (
    ActionResult,
    FieldResult,
    FieldSpec,
    FillAction,
    FillReport,
    PageState,
)

__all__ = [
    "ActionExecutor",
    "ActionResult",
    "CDPBrowserManager",
    "EvidenceManager",
    "FieldResult",
    "FieldSpec",
    "FillAction",
    "FillReport",
    "PageState",
    "PageStateClassifier",
]

