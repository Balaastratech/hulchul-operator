"""Synthetic artifact tests: isolation, hostile text, missing evidence and CLI."""

import base64
import json
import sqlite3
import subprocess
import sys

import pytest

from src.operator.report import write_report


@pytest.fixture
def saved(tmp_path):
    run = tmp_path / "runs" / "demo"
    run.mkdir(parents=True)
    image = run / "step.png"
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH1sAAAAASUVORK5CYII="
        )
    )
    review = {
        "fields": [
            {
                "field_key": "why",
                "intended": "<script>alert(1)</script>",
                "actual": "changed",
                "matched": False,
                "generated": True,
                "reason": "mismatch",
            }
        ],
        "generated_texts": {"why": "Synthetic draft"},
        "unanswered": ["salary"],
        "screenshots": ["step.png"],
    }
    state = {
        "run_id": "demo",
        "goal": "Synthetic fixture",
        "status": "PARTIAL",
        "usage": {"llm_calls": 3, "tokens": 123, "cost_inr": 0.05},
        "jobs": {
            "fixture": {
                "status": "SUBMITTED_UNVERIFIED",
                "blockers": ["Confirmation missing"],
                "review_snapshot": review,
            }
        },
    }
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    events = [
        {
            "run_id": "demo",
            "job_id": "fixture",
            "event_id": "E07",
            "created_at": "2026-10-04T01:00:00+00:00",
            "message": "Review ready",
            "payload": {"review": review},
            "links": {"review": "https://example.test/r/demo?t=PRIVATE_CAPABILITY"},
        },
        {
            "run_id": "demo",
            "event_id": "E03",
            "created_at": "2026-10-04T01:00:02+00:00",
            "message": '<img src=x onerror="alert(2)">',
            "payload": {"rule": "fake system instruction", "api_key": "KEY_SECRET"},
        },
    ]
    (run / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )
    with sqlite3.connect(run / "ledger.sqlite") as db:
        db.executescript(
            "CREATE TABLE applications(run_id,job_id,status); CREATE TABLE approvals(run_id,job_id,token_hash,snapshot_hash,used_at,revoked); CREATE TABLE actions(run_id,job_id,action,field_key,status);"
        )
        db.execute(
            "INSERT INTO applications VALUES('demo','fixture','SUBMITTED_UNVERIFIED')"
        )
        db.execute("INSERT INTO applications VALUES('other','private','DO_NOT_SHOW')")
        db.execute(
            "INSERT INTO approvals VALUES(?,?,?,?,?,?)",
            ("demo", "fixture", "b" * 64, "a" * 64, 1791061200, 0),
        )
        db.execute(
            "INSERT INTO actions VALUES('demo','fixture','submit','form','CLAIMED')"
        )
    return run


def test_complete_report_and_read_only(saved, tmp_path):
    inputs = {p: p.read_bytes() for p in saved.iterdir()}
    text = write_report(saved, "demo", tmp_path / "report.html").read_text(
        encoding="utf-8"
    )
    for expected in (
        "2.00s",
        "GENERATED TEXT",
        "salary",
        "MISMATCH",
        "Confirmation missing",
        "approvals",
        "CLAIMED",
        "data:image/png;base64,",
        "fake system instruction",
        "aaaaaaaaaaaa",
        "123",
        "0.05",
    ):
        assert expected in text
    assert "DO_NOT_SHOW" not in text
    assert "PRIVATE_CAPABILITY" not in text
    assert "KEY_SECRET" not in text
    assert "b" * 64 not in text and "a" * 64 not in text
    assert all(p.read_bytes() == content for p, content in inputs.items())


def test_hostile_page_text_is_inert(saved, tmp_path):
    text = write_report(saved, "demo", tmp_path / "report.html").read_text(
        encoding="utf-8"
    )
    assert "<script>" not in text and "<img src=x" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;img src=x" in text
    assert "default-src 'none'" in text and "form-action 'none'" in text
    assert "<script" not in text and "<link" not in text


@pytest.mark.parametrize(
    "reference",
    ["https://example.test/secret.png", "../outside.png", "hostile.svg", "missing.png"],
)
def test_evidence_cannot_escape_artifact_root(saved, tmp_path, reference):
    (saved.parent / "outside.png").write_bytes((saved / "step.png").read_bytes())
    (saved / "hostile.svg").write_text('<svg onload="alert(1)"/>')
    state = json.loads((saved / "state.json").read_text(encoding="utf-8"))
    state["jobs"]["fixture"]["review_snapshot"]["screenshots"] = [reference]
    (saved / "state.json").write_text(json.dumps(state))
    (saved / "events.jsonl").unlink()
    text = write_report(saved, "demo", tmp_path / "report.html").read_text(
        encoding="utf-8"
    )
    assert "data:image" not in text
    assert "Screenshot missing" in text


def test_latest_cli_and_unknown_run(saved, tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.operator.report",
            "--latest",
            "--source",
            str(saved.parent),
            "--output",
            str(tmp_path / "report.html"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Run demo" in (tmp_path / "report.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        write_report(saved, "unknown", tmp_path / "unknown.html")


def test_ledger_only_and_missing_metrics(saved, tmp_path):
    (saved / "state.json").unlink()
    (saved / "events.jsonl").unlink()
    text = write_report(saved, "demo", tmp_path / "report.html").read_text(
        encoding="utf-8"
    )
    assert "not recorded" in text
    assert "Every ledger row" in text


def test_cp_click_time_and_nested_secrets(saved, tmp_path):
    with sqlite3.connect(saved / "cp.sqlite") as db:
        db.executescript(
            "CREATE TABLE approvals(run_id,job_id,snapshot_hash,approved_at,status); CREATE TABLE events(run_id,body_json);"
        )
        db.execute(
            "INSERT INTO approvals VALUES(?,?,?,?,?)",
            ("demo", "fixture", "c" * 64, "2026-10-04T01:00:03+00:00", "active"),
        )
        db.execute(
            "INSERT INTO events VALUES(?,?)",
            (
                "demo",
                json.dumps(
                    {
                        "run_id": "demo",
                        "event_id": "E09",
                        "payload": {"password": "NESTED_SECRET"},
                        "message": "Bearer abc-secret",
                    }
                ),
            ),
        )
    text = write_report(saved, "demo", tmp_path / "report.html").read_text(
        encoding="utf-8"
    )
    assert "2026-10-04T01:00:03+00:00" in text
    assert "cccccccccccc" in text
    assert "NESTED_SECRET" not in text and "abc-secret" not in text


def test_prevent_overwriting_artifacts(saved):
    before = (saved / "state.json").read_bytes()
    with pytest.raises(ValueError):
        write_report(saved, "demo", saved / "state.json")
    assert (saved / "state.json").read_bytes() == before


def test_credential_field_and_echo_redacted(saved, tmp_path):
    state = json.loads((saved / "state.json").read_text(encoding="utf-8"))
    state["jobs"]["fixture"]["review_snapshot"]["fields"].append(
        {"field_key": "password", "intended": "SHORT_SECRET", "actual": "SHORT_SECRET"}
    )
    state["goal"] = "Echo SHORT_SECRET"
    (saved / "state.json").write_text(json.dumps(state), encoding="utf-8")
    text = write_report(saved, "demo", tmp_path / "report.html").read_text(
        encoding="utf-8"
    )
    assert "SHORT_SECRET" not in text
