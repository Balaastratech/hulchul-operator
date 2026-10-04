"""S9-style unseen local layouts and adversarial read-back checks."""
import ast
from pathlib import Path

import pytest

from src.operator.browser.classify import PageStateClassifier
from src.operator.browser.execute import ActionExecutor
from src.operator.browser.extract import FieldExtractor
from src.operator.browser.models import FillAction, PageState
from src.operator.browser.navigate import StepNavigator
from src.operator.browser.verify import FuzzyVerifier


def test_continue_submit_is_never_clicked(page):
    page.set_content('''<form onsubmit="window.submits++;return false">
    <button type="submit">Continue</button></form><script>window.submits=0</script>''')
    StepNavigator(page).click_next(wait_time=0)
    assert page.evaluate("window.submits") == 0


def test_combobox_enter_does_not_submit(page):
    page.set_content('''<form onsubmit="window.submits++;return false">
    <label for="city">City</label><input id="city" role="combobox">
    <button type="submit">Submit</button></form><script>window.submits=0</script>''')
    field = FieldExtractor().extract_fields(page)[0]
    ActionExecutor(page).execute_action(FillAction(field_key=field.key, action="fill", value="Delhi"), field)
    assert page.evaluate("window.submits") == 0


@pytest.mark.parametrize("expected,actual,label", [
    ("aarav@example.com", "evil-aarav@example.com", "Email"),
    ("1200000", "120000", "Salary"),
    ("No sponsorship required", "Sponsorship required", "Sponsorship"),
    ("+91 9876543210", "+1 9876543210", "Phone"),
    ("resume.pdf", "other.pdf", "Resume"),
])
def test_verifier_rejects_materially_different_answers(expected, actual, label):
    assert not FuzzyVerifier().is_match(expected, actual, label)[0]


def test_yes_no_widget_can_be_executed(page):
    page.set_content('''<fieldset role="radiogroup" aria-label="Relocate">
    <button type="button" onclick="this.setAttribute('aria-pressed','true')">Yes</button>
    <button type="button">No</button></fieldset>''')
    field = FieldExtractor().extract_fields(page)[0]
    result = ActionExecutor(page).execute_action(FillAction(field_key=field.key, action="select", value="Yes"), field)
    assert result.success
    assert FieldExtractor().extract_fields(page)[0].current_value == "Yes"


def test_checkbox_false_is_not_checked(page):
    page.set_content('<label><input type="checkbox">Relocate</label>')
    field = FieldExtractor().extract_fields(page)[0]
    ActionExecutor(page).execute_action(FillAction(field_key=field.key, action="check", value=False), field)
    assert not page.locator("input").is_checked()


def test_confirmation_phrase_does_not_override_password_wall(page):
    page.set_content('<p>Sign in to see whether your application has been received</p><input type="password">')
    assert PageStateClassifier().classify_page(page) == PageState.LOGIN


def test_safe_layout_extract_fill_readback_and_handoffs(page):
    page.set_content('<label for="name">Name</label><input id="name"><button type="submit">Apply</button>')
    field = FieldExtractor().extract_fields(page)[0]
    result = ActionExecutor(page).execute_action(FillAction(field_key=field.key, action="fill", value="Synthetic Person"), field)
    assert result.success
    assert FuzzyVerifier().is_match("Synthetic Person", FieldExtractor().extract_fields(page)[0].current_value)[0]
    assert StepNavigator(page).find_next_button() is None
    page.set_content('<input type="password">')
    assert PageStateClassifier().classify_page(page) == PageState.LOGIN
    page.set_content('<iframe title="challenge" style="width:300px;height:300px"></iframe>')
    assert PageStateClassifier().classify_page(page) == PageState.CAPTCHA


@pytest.mark.xfail(strict=True, reason="AUDIT-027")
def test_s10_documented_40_of_40_is_reproducible():
    source = Path(__file__).resolve().parents[2] / "tests/test_extract_verify.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "test_fuzzy_verifier_40_pairs_benchmark")
    assignment = next(node for node in function.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "test_pairs" for target in node.targets))
    pairs = ast.literal_eval(assignment.value)
    correct = sum(FuzzyVerifier().is_match(expected, actual, label)[0] == matched for expected, actual, matched, label in pairs)
    assert correct == 40
