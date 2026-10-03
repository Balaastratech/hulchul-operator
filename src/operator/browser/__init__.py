"""Browser operator package exporting CDP, execute, evidence, and models."""

from src.operator.browser.cdp import CDPBrowserManager
from src.operator.browser.classify import PageStateClassifier
from src.operator.browser.evidence import EvidenceManager
from src.operator.browser.execute import ActionExecutor
from src.operator.browser.extract import FieldExtractor, build_stable_key
from src.operator.browser.models import (
    ActionResult,
    FieldResult,
    FieldSpec,
    FillAction,
    FillReport,
    PageState,
)
from src.operator.browser.navigate import (
    StepNavigator,
    is_safe_next_button,
    is_submit_class_button,
)
from src.operator.browser.verify import FuzzyVerifier, normalise_value

__all__ = [
    "ActionExecutor",
    "ActionResult",
    "CDPBrowserManager",
    "EvidenceManager",
    "FieldExtractor",
    "FieldResult",
    "FieldSpec",
    "FillAction",
    "FillReport",
    "FuzzyVerifier",
    "PageState",
    "PageStateClassifier",
    "StepNavigator",
    "build_stable_key",
    "is_safe_next_button",
    "is_submit_class_button",
    "normalise_value",
]



