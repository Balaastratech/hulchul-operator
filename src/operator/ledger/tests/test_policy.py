"""Policy tests kept in Codex-owned test paths."""

import pytest

from src.operator.contracts import AnswerLibrary, FieldSpec, FillAction, Goal, Profile, Rules
from src.operator.policy.allowlist import DomainAllowlist
from src.operator.policy.authority import authorize_submit, check_fill
from src.operator.policy.tiers import Tier, action_tier


def decision(label, *, value="Synthetic", source="profile.name", action="fill", rules=None):
    field = FieldSpec(id="f", key="f", label=label, type="text")
    return check_fill(FillAction(field_key="f", action=action, value=value, source=source), field,
                      url="http://localhost:8000/form", allowlist=DomainAllowlist.from_urls(["http://localhost:8000"]),
                      goal=Goal(), profile=Profile(name="Synthetic", email="synthetic@example.test"),
                      rules=rules or Rules(), answers=AnswerLibrary(), resume_path="synthetic.pdf")


@pytest.mark.parametrize("label", ["Gender", "Race", "Disability", "Veteran", "I certify accuracy", "Consent", "Password", "CAPTCHA"])
def test_sensitive_and_legal_controls_never_autofilled(label):
    result = decision(label)
    assert not result.allowed
    assert result.action.action == "ask_user"


def test_source_facts_enforced_against_forced_malicious_plan():
    assert decision("Full name").allowed
    assert not decision("Full name", value="Invented", source="page.system").allowed
    assert not decision("Full name", value="Invented", source="profile.name").allowed
    assert decision("Gender", rules=Rules(eeo_policy="leave_blank")).action.action == "skip"


def test_exact_host_and_credential_redirect_defense():
    allowed = DomainAllowlist.from_urls(["https://example.test"])
    for url in ["https://example.test.attacker.test", "https://evil.test@example.test", "javascript:alert(1)",
                "https://example.test\\@evil.test", "https://sub.example.test"]:
        assert not allowed.permits(url)
    with pytest.raises(PermissionError):
        allowed.check_redirects(["https://example.test", "https://evil.test", "https://example.test"])


def test_only_explicit_fixture_origin_and_normal_goal_can_submit():
    allowed = DomainAllowlist.from_urls(["https://employer.test", "http://localhost:8000"],
                                        fixture_urls=["http://localhost:8000"])
    for url, goal, live in [("https://employer.test", Goal(mode="normal"), "a"),
                            ("http://localhost:8000", Goal(), "a"),
                            ("http://localhost:8001", Goal(mode="normal"), "a"),
                            ("http://localhost:8000", Goal(mode="normal"), "b")]:
        with pytest.raises(PermissionError):
            authorize_submit(url=url, allowlist=allowed, goal=goal, approved_hash="a", live_hash=live)
    authorize_submit(url="http://localhost:8000", allowlist=allowed,
                     goal=Goal(mode="normal"), approved_hash="a", live_hash="a")


def test_unknown_authority_fails_closed():
    assert action_tier("solve_captcha") == Tier.FORBIDDEN
    assert action_tier("new_model_invented_action") == Tier.FORBIDDEN
    assert action_tier("consent") == Tier.EXTERNAL
