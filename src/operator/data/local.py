"""Local folder data source adapter."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any
import yaml

from src.operator.data.protocol import DataSourcePort
from src.operator.data.schema import (
    Answer,
    AnswerLibrary,
    DataSnapshot,
    JobPosting,
    Profile,
    Rules,
)

logger = logging.getLogger(__name__)


def compute_file_sha256(file_path: Path) -> str:
    """Compute hex SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def parse_profile_file(file_path: Path) -> Profile:
    """Parse profile.json or profile.md with YAML frontmatter."""
    text = file_path.read_text(encoding="utf-8")
    if file_path.suffix.lower() == ".json":
        data = json.loads(text)
        return Profile.model_validate(data)

    # Markdown format with optional UTF-8 BOM and YAML frontmatter
    text = text.lstrip("\ufeff")
    frontmatter_dict: dict[str, Any] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter_dict = yaml.safe_load(parts[1]) or {}
            except Exception as e:
                logger.warning("Failed to parse YAML frontmatter in %s: %s", file_path, e)

    data = dict(frontmatter_dict)
    # Map any differences between sample_data/profile.md and Profile schema
    loc_prefs = data.get("location_preferences", {})
    cities = loc_prefs.get("preferred_cities", []) if isinstance(loc_prefs, dict) else []
    city = data.get("city", "")
    country = data.get("country", "")
    location_str = f"{city}, {country}".strip(", ") or None

    mapped: dict[str, Any] = {
        "name": data.get("name") or "Candidate",
        "email": data.get("email") or "candidate@example.test",
        "phone": data.get("phone"),
        "location": location_str,
        "links": data.get("links", {}),
        "education": data.get("education", []),
        "experience": data.get("experience", []),
        "skills": data.get("skills", []),
        "work_authorization": data.get("work_authorization"),
        "sponsorship": data.get("requires_sponsorship", False),
        "relocation": loc_prefs.get("willing_to_relocate", False) if isinstance(loc_prefs, dict) else False,
        "notice_period": data.get("notice_period"),
        "location_prefs": cities,
    }
    return Profile.model_validate(mapped)


def parse_rules_file(file_path: Path) -> Rules:
    """Parse rules.md with optional YAML front-matter or code block."""
    text = file_path.read_text(encoding="utf-8").lstrip("\ufeff")
    frontmatter_dict: dict[str, Any] = {}
    prose = text

    # Check for ```yaml ... ``` code block first
    m = re.search(r"```yaml\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            frontmatter_dict = yaml.safe_load(m.group(1)) or {}
            prose = (text[:m.start()] + text[m.end():]).strip()
        except Exception as e:
            logger.warning("Failed to parse YAML block in %s: %s", file_path, e)
    elif text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter_dict = yaml.safe_load(parts[1]) or {}
                prose = parts[2].strip()
            except Exception as e:
                logger.warning("Failed to parse YAML frontmatter in %s: %s", file_path, e)

    data = dict(frontmatter_dict)
    # Normalize eeo_policy if variant is given
    eeo = data.get("eeo_policy")
    if eeo in ("decline_to_answer", "decline_to_self_identify"):
        data["eeo_policy"] = "decline"

    data["prose"] = prose
    return Rules.model_validate(data)


def parse_answers_csv(file_path: Path) -> AnswerLibrary:
    """Parse answers.csv into AnswerLibrary."""
    answers: list[Answer] = []
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pattern = row.get("pattern", "").strip()
            answer_text = row.get("answer", "").strip()
            if not pattern:
                continue
            sens = row.get("sensitivity", "normal").strip().lower()
            if sens not in ("normal", "sensitive", "legal"):
                sens = "normal"
            source = row.get("source", "candidate").strip() or "candidate"
            answers.append(
                Answer(
                    pattern=pattern,
                    answer=answer_text,
                    sensitivity=sens,  # type: ignore
                    source=source,
                )
            )
    return AnswerLibrary(answers=answers)


def parse_job_queue_csv(file_path: Path) -> list[JobPosting]:
    """Parse job_queue.csv into list of JobPosting objects."""
    jobs: list[JobPosting] = []
    if not file_path.exists():
        return jobs

    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            url = row.get("url", "").strip()
            if not url:
                continue
            company = row.get("company", "").strip() or "Unknown Company"
            title = row.get("title", "").strip() or "Software Engineer"
            job_id = f"job-{i+1:03d}"
            # Preserve job ID from URL if present (e.g., /jobs/job-1001 -> job-1001)
            m = re.search(r"/jobs/(job-\d+)", url)
            if m:
                job_id = m.group(1)
            jobs.append(
                JobPosting(
                    job_id=job_id,
                    url=url,
                    company=company,
                    title=title,
                )
            )
    return jobs


class LocalFolderDataSource(DataSourcePort):
    """Data source reading from a local folder of files."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir).resolve()

    def load_sync(self, run_id: str) -> DataSnapshot:
        """Synchronously load files from local folder, compute hashes, snapshot to runs/."""
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"Data directory not found: {self.data_dir}")

        profile_path = self.data_dir / "profile.json"
        if not profile_path.exists():
            if (self.data_dir / "profile.md").exists():
                profile_path = self.data_dir / "profile.md"
            else:
                profile_path = self.data_dir / "profile.json"

        rules_path = self.data_dir / "rules.md"
        answers_path = self.data_dir / "answers.csv"
        resume_path = self.data_dir / "resume.pdf"
        job_queue_path = self.data_dir / "job_queue.csv"

        for required in (profile_path, rules_path, answers_path, resume_path):
            if not required.exists():
                raise FileNotFoundError(f"Required candidate data file missing: {required.name} in {self.data_dir}")

        file_hashes: dict[str, str] = {}
        for p in (profile_path, rules_path, answers_path, resume_path, job_queue_path):
            if p.exists():
                file_hashes[p.name] = compute_file_sha256(p)

        # Compute composite snapshot hash
        combined_hash_input = "".join(f"{k}:{v};" for k, v in sorted(file_hashes.items()))
        snapshot_hash = hashlib.sha256(combined_hash_input.encode("utf-8")).hexdigest()

        # Parse objects
        profile = parse_profile_file(profile_path)
        rules = parse_rules_file(rules_path)
        answer_lib = parse_answers_csv(answers_path)
        jobs = parse_job_queue_csv(job_queue_path)

        # Snapshot files to runs/<run_id>/data/
        run_data_dir = Path("runs") / run_id / "data"
        run_data_dir.mkdir(parents=True, exist_ok=True)
        for p in (profile_path, rules_path, answers_path, resume_path, job_queue_path):
            if p.exists():
                shutil.copy2(p, run_data_dir / p.name)

        return DataSnapshot(
            profile=profile,
            rules=rules,
            answer_library=answer_lib,
            resume_path=str(run_data_dir / resume_path.name),
            resume_hash=file_hashes["resume.pdf"],
            jobs=jobs,
            snapshot_hash=snapshot_hash,
            file_hashes=file_hashes,
        )

    async def load(self, run_id: str) -> DataSnapshot:
        """Asynchronously load data snapshot."""
        return self.load_sync(run_id)
