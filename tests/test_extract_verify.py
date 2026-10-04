"""Tests for FieldExtractor and FuzzyVerifier (S10)."""

import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright

from src.operator.browser.extract import FieldExtractor, build_stable_key
from src.operator.browser.models import FieldSpec, FillAction
from src.operator.browser.verify import FuzzyVerifier, normalise_value


def test_build_stable_key():
    """Verify stable key format: label|type|group|n."""
    k = build_stable_key("First Name", "text", "", 0)
    assert k == "First Name|text||0"
    k2 = build_stable_key("Authorized", "checkbox", "Legal Disclosures", 1)
    assert k2 == "Authorized|checkbox|Legal Disclosures|1"


def test_fuzzy_verifier_40_pairs_benchmark():
    """Spike S10 benchmark: 40 intended-vs-actual pairs tested against deterministic normaliser."""
    verifier = FuzzyVerifier()

    test_pairs = [
        # (intended, actual, expected_match, label)
        # Location variants
        ("Ahmedabad, India", "Ahmedabad, Gujarat, India", True, "Location"),
        ("New York, USA", "New York, NY", True, "City"),
        ("Bengaluru", "Bangalore", False, "City"),  # semantic difference without LLM
        ("London, UK", "London, United Kingdom", True, "Location"),
        ("San Francisco", "San Francisco, CA", True, "City"),

        # Phone numbers
        ("+91 98765 43210", "9876543210", True, "Phone"),
        ("9876543210", "+91-98765-43210", True, "Mobile"),
        ("123-456-7890", "(123) 456-7890", True, "Contact Number"),
        ("+1 555 123 4567", "5551234567", True, "Phone"),
        ("9876543210", "1234567890", False, "Phone"),

        # Numeric and experience formats
        ("3 years", "3", True, "Years of Experience"),
        ("5", "5 years", True, "Experience"),
        ("10", "10+", True, "Years"),
        ("1200000", "1200000", True, "Salary"),
        ("0", "0 years", True, "Experience"),
        ("2", "4", False, "Years"),

        # Boolean / Checkbox variants
        ("true", "true", True, "Authorized"),
        ("yes", "true", True, "Willing to relocate"),
        ("1", "true", True, "Terms accepted"),
        ("No", "false", True, "Sponsorship needed"),
        ("false", "false", True, "Requires Visa"),
        ("true", "false", False, "Consent"),

        # Whitespace, punctuation and casing
        ("Aarav Mehta", "aarav mehta", True, "Full Name"),
        ("Software Engineer", " Software   Engineer  ", True, "Title"),
        ("TypeScript, React", "TypeScript React", True, "Skills"),
        ("Acme Corp.", "Acme Corp", True, "Company"),
        ("https://github.com/aarav", "https://github.com/aarav/", True, "GitHub"),
        ("aarav@example.com", "AARAV@EXAMPLE.COM", True, "Email"),

        # Resume file upload
        ("/path/to/resume.pdf", "resume.pdf", True, "Resume"),
        ("resume.pdf", "resume_aarav.pdf", True, "Resume / CV"),
        ("resume.pdf", "", False, "Resume"),

        # Name & text variants
        ("John Doe", "Johnathan Doe", False, "Name"),
        ("Full-stack Engineer", "Senior Full-stack Engineer", True, "Role"),
        ("Immediate", "Immediately", True, "Notice Period"),
        ("30 days", "1 month (30 days)", True, "Notice Period"),
        ("India", "India", True, "Country"),
        ("Remote", "Remote - Worldwide", True, "Workplace preference"),
        ("Python", "Python 3.12", True, "Programming language"),
        ("PostgreSQL", "Postgres", True, "Database"),
        ("Bachelor's Degree", "B.Tech in Computer Science", True, "Education"),
    ]


    assert len(test_pairs) == 40

    correct_judgments = 0
    deterministic_success = 0

    for intended, actual, expected_match, label in test_pairs:
        matched, _ = verifier.is_match(intended, actual, field_label=label)
        if matched == expected_match:
            correct_judgments += 1
        if matched:
            deterministic_success += 1

    accuracy = correct_judgments / len(test_pairs)
    # S10 pass criteria: >= 95% judged correctly
    assert accuracy >= 0.95, f"Expected >= 95% accuracy, got {accuracy * 100:.1f}% ({correct_judgments}/40)"


def test_extractor_on_html(tmp_path: Path):
    """Test FieldExtractor on diverse inputs including button yes/no and custom checkboxes."""
    html = """
    <!DOCTYPE html>
    <html>
    <body>
      <form>
        <div class="field">
          <label for="name">Full Name *</label>
          <input type="text" id="name" name="full_name" value="Aarav">
        </div>
        <div class="field">
          <label>Are you authorized to work in India? *</label>
          <div class="yes-no-group" role="radiogroup" aria-label="Authorized to work in India">
            <button type="button">Yes</button>
            <button type="button">No</button>
          </div>
        </div>
        <div class="field">
          <label>
            <input type="checkbox" style="display: none;" name="agree" value="1">
            <span class="custom-box">I agree to terms</span>
          </label>
        </div>
      </form>
    </body>
    </html>
    """
    f = tmp_path / "test_extract.html"
    f.write_text(html, encoding="utf-8")

    extractor = FieldExtractor()

    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        p = b.new_page()
        p.goto(f"file:///{f.as_posix()}")

        fields = extractor.extract_fields(p)
        assert len(fields) >= 3

        labels = [field.label for field in fields]
        types = [field.type for field in fields]

        assert any("Full Name" in l for l in labels)
        assert any(t == "yes_no_button" for t in types)
        assert any("agree to terms" in l for l in labels)

        b.close()


def test_fuzzy_verifier_negative_pairs_hardening():
    """Independent adversarial negative test pairs for verifier hardening (AUDIT-003)."""
    verifier = FuzzyVerifier()

    negative_pairs = [
        # (intended, actual, label, description)
        ("aarav@example.com", "evil-aarav@example.com", "Email", "Email prefix spoofing"),
        ("candidate@company.com", "candidate@company.org", "Email", "Domain TLD difference"),
        ("1200000", "120000", "Salary", "Salary 10x magnitude mismatch"),
        ("50000", "500000", "CTC", "CTC 10x magnitude mismatch"),
        ("No sponsorship required", "Sponsorship required", "Sponsorship", "Sponsorship polarity inversion"),
        ("Authorized to work", "Not authorized to work", "Work Authorization", "Authorization negation"),
        ("+91 9876543210", "+1 9876543210", "Phone", "International country code difference"),
        ("+44 7911 123456", "+1 7911 123456", "Phone", "UK vs US country code difference"),
        ("resume.pdf", "other.pdf", "Resume", "Entirely different filename"),
        ("cv_aarav.pdf", "malicious_payload.pdf", "Resume", "Different resume name"),
        ("true", "false", "Consent", "Direct boolean contradiction"),
        ("yes", "no", "Relocation", "Direct affirmative vs negative"),
    ]

    for intended, actual, label, desc in negative_pairs:
        matched, reason = verifier.is_match(intended, actual, field_label=label)
        assert not matched, f"Failed rejection on '{desc}': intended='{intended}' actual='{actual}' (reason: {reason})"
