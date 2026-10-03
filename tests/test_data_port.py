"""Unit tests for Data Port and adapters."""

import json
import pytest
from pathlib import Path

from src.operator.data.drive import DrivePublicDataSource
from src.operator.data.factory import get_data_source
from src.operator.data.local import LocalFolderDataSource
from src.operator.data.protocol import DataSourcePort
from src.operator.data.schema import DataSnapshot


@pytest.fixture
def temp_sample_data(tmp_path: Path) -> Path:
    """Create a temporary valid candidate data directory."""
    d = tmp_path / "candidate_data"
    d.mkdir()

    profile = {
        "name": "Aarav Mehta",
        "email": "aarav.mehta@example.com",
        "phone": "+91 98765 43210",
        "location": "Ahmedabad, India",
        "links": {"linkedin": "https://linkedin.com/in/aarav-mehta"},
        "skills": ["Python", "TypeScript", "React"],
        "education": [{"school": "IIT", "degree": "B.Tech"}],
        "experience": [{"company": "Acme", "title": "SWE", "years": 3}],
        "work_authorization": "Authorized in India",
        "sponsorship": False,
        "relocation": False,
        "notice_period": "30 days",
    }
    (d / "profile.json").write_text(json.dumps(profile), encoding="utf-8")

    rules = (
        "---\n"
        "min_salary: 1200000\n"
        "remote_only: true\n"
        "blocked_companies: ['HostileCorp']\n"
        "target_roles: ['Software Engineer']\n"
        "---\n"
        "Apply only to roles with modern tech stack. Never invent facts."
    )
    (d / "rules.md").write_text(rules, encoding="utf-8")

    answers = (
        "pattern,answer,sensitivity,source,updated\n"
        "notice period,30 days,normal,candidate,\n"
        "willing to relocate,No,normal,candidate,\n"
        "expected salary,15 LPA,normal,candidate,\n"
    )
    (d / "answers.csv").write_text(answers, encoding="utf-8")

    # Resume binary dummy
    (d / "resume.pdf").write_bytes(b"%PDF-1.4 sample resume Aarav Mehta")

    job_queue = (
        "url,company,title,added_by\n"
        "https://jobs.lever.co/palantir/123,Palantir,SWE,user\n"
        "https://job-boards.greenhouse.io/vercel/456,Vercel,Frontend,user\n"
    )
    (d / "job_queue.csv").write_text(job_queue, encoding="utf-8")

    return d


def test_data_port_protocol_conformance(temp_sample_data: Path):
    """Verify LocalFolderDataSource satisfies DataSourcePort protocol."""
    ds = LocalFolderDataSource(temp_sample_data)
    assert isinstance(ds, DataSourcePort)


def test_load_local_folder(temp_sample_data: Path):
    """Verify loading from local folder creates valid DataSnapshot with hashes."""
    ds = LocalFolderDataSource(temp_sample_data)
    snapshot = ds.load_sync("run-test-001")

    assert snapshot.profile.name == "Aarav Mehta"
    assert snapshot.profile.email == "aarav.mehta@example.com"
    assert snapshot.rules.min_salary == 1200000
    assert snapshot.rules.remote_only is True
    assert len(snapshot.answer_library.answers) == 3
    assert len(snapshot.jobs) == 2
    assert snapshot.snapshot_hash
    assert len(snapshot.snapshot_hash) == 64
    assert len(snapshot.resume_hash) == 64

    # Verify snapshot files were saved to runs/run-test-001/data/
    copied_resume = Path(snapshot.resume_path)
    assert copied_resume.exists()
    assert copied_resume.read_bytes() == b"%PDF-1.4 sample resume Aarav Mehta"


def test_hash_changes_on_file_modification(temp_sample_data: Path):
    """Verify that editing a candidate file alters snapshot_hash."""
    ds = LocalFolderDataSource(temp_sample_data)
    snap1 = ds.load_sync("run-test-001")

    # Modify answers file
    answers_file = temp_sample_data / "answers.csv"
    answers_file.write_text(answers_file.read_text() + "visa status,citizen,normal,candidate,\n")

    snap2 = ds.load_sync("run-test-002")
    assert snap1.snapshot_hash != snap2.snapshot_hash
    assert snap1.file_hashes["answers.csv"] != snap2.file_hashes["answers.csv"]


def test_missing_required_file_raises_error(temp_sample_data: Path):
    """Verify missing required file raises FileNotFoundError with precise name."""
    (temp_sample_data / "profile.json").unlink()
    ds = LocalFolderDataSource(temp_sample_data)

    with pytest.raises(FileNotFoundError, match="profile.json"):
        ds.load_sync("run-test-003")


@pytest.mark.anyio
async def test_async_load(temp_sample_data: Path):
    """Verify async load method."""
    ds = LocalFolderDataSource(temp_sample_data)
    snapshot = await ds.load("run-test-async")
    assert snapshot.profile.name == "Aarav Mehta"


def test_drive_public_fallback(temp_sample_data: Path):
    """Verify DrivePublicDataSource falls back to local directory when folder_id is absent."""
    drive_ds = DrivePublicDataSource(folder_id="", fallback_dir=temp_sample_data)
    snapshot = drive_ds.load_sync("run-drive-fallback")
    assert snapshot.profile.name == "Aarav Mehta"
