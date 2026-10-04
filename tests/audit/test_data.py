"""Data consistency probes using synthetic sample files and fake downloads."""
import json
import shutil

import pytest

from src.operator.data.drive import DrivePublicDataSource
from src.operator.data.local import LocalFolderDataSource, parse_rules_file


def synthetic_source(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "profile.json").write_text(json.dumps({"name": "Synthetic Person", "email": "synthetic@example.test"}), encoding="utf-8")
    (directory / "rules.md").write_text("---\nremote_only: true\n---\nSynthetic rules", encoding="utf-8")
    (directory / "answers.csv").write_text("pattern,answer,sensitivity,source\nnotice,30 days,normal,candidate\n", encoding="utf-8")
    (directory / "resume.pdf").write_bytes(b"%PDF-1.4 synthetic")
    return directory


def test_drive_profile_doc_is_used(monkeypatch, audit_dir):
    samples = synthetic_source(audit_dir / "source")
    def download(url, dest, **kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        source = samples / ("profile.json" if dest.name == "profile.md" else dest.name)
        shutil.copy2(source, dest)
        return True
    monkeypatch.setattr("src.operator.data.drive.download_url", download)
    monkeypatch.chdir(audit_dir)
    source = DrivePublicDataSource(file_ids={"profile": "p", "rules": "r", "answers": "a", "resume": "b"}, cache_dir=audit_dir / "cache", fallback_dir=None)
    source.load_sync("audit-run")


def test_bom_does_not_discard_hard_rules(audit_dir):
    rules = audit_dir / "rules.md"
    rules.write_text('\ufeff---\nremote_only: true\nblocked_companies: [BadCo]\n---\nSynthetic rules', encoding="utf-8")
    result = parse_rules_file(rules)
    assert result.remote_only and result.blocked_companies == ["BadCo"]


@pytest.mark.xfail(strict=True, reason="AUDIT-017")
def test_remote_filename_cannot_escape_cache(monkeypatch, audit_dir):
    def download(url, dest, **kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"synthetic")
        return True
    monkeypatch.setattr("src.operator.data.drive.download_url", download)
    source = DrivePublicDataSource(file_ids={"../escaped.pdf": "fake"}, fallback_dir=None)
    source._sync_drive_files(audit_dir / "cache")
    assert not (audit_dir / "escaped.pdf").exists()


@pytest.mark.xfail(strict=True, reason="AUDIT-018")
def test_partial_refresh_never_mixes_stale_rules(monkeypatch, audit_dir):
    cache = audit_dir / "cache" / "default"
    synthetic_source(cache)
    def download(url, dest, **kwargs):
        if dest.name == "rules.md":
            dest.write_text("---\nremote_only: false\n---\nNew rules", encoding="utf-8")
            return True
        return False
    monkeypatch.setattr("src.operator.data.drive.download_url", download)
    monkeypatch.chdir(audit_dir)
    source = DrivePublicDataSource(file_ids={"profile.json": "p", "rules": "r"}, cache_dir=audit_dir / "cache", fallback_dir=None)
    with pytest.raises((FileNotFoundError, RuntimeError)):
        source.load_sync("partial-run")


@pytest.mark.xfail(strict=True, reason="AUDIT-019")
def test_run_id_cannot_escape_runs(monkeypatch, audit_dir):
    monkeypatch.chdir(audit_dir)
    source = synthetic_source(audit_dir / "source")
    LocalFolderDataSource(source).load_sync("../escaped-run")
    assert not (audit_dir / "escaped-run" / "data" / "profile.json").exists()
