"""Compatibility at composition boundary; peer data modules remain unchanged."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import yaml

from src.operator.contracts import DataSnapshot, Profile, Rules
from src.operator.data.drive import DrivePublicDataSource
from src.operator.data.local import LocalFolderDataSource
from src.operator.policy.allowlist import DomainAllowlist


class PostingHTML(HTMLParser):
    """Read posting metadata including hidden text/comments as untrusted data."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.apply_links: list[str] = []
        self.feed(html)

    def handle_data(self, data: str) -> None:
        self.text.append(data)

    def handle_comment(self, data: str) -> None:
        self.text.append(data)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        values = dict(attrs)
        if tag == "a" and "apply" in values.get("class", "").split():
            self.apply_links.append(values.get("href", ""))


def yaml_block(text: str) -> dict:
    """Accept the documented front matter or fenced hard-constraints YAML."""
    text = text.lstrip("\ufeff")
    if text.startswith("---"):
        return yaml.safe_load(text.split("---", 2)[1]) or {}
    match = re.search(r"```yaml\s*\n(.*?)```", text, re.DOTALL)
    return yaml.safe_load(match.group(1)) if match else {}


def normalize_source(source: Path, destination: Path) -> dict[str, str]:
    """Translate documented synthetic formats and preserve original file hashes."""
    destination.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for path in source.iterdir():
        if path.is_file() and path.suffix in {".md", ".json", ".csv", ".pdf"}:
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.resolve() != (destination / path.name).resolve():
                shutil.copy2(path, destination / path.name)
    # Google Sheets CSV exports need not end in a newline. Rewrite rows before appending.
    with (destination / "answers.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        rows = list(reader)
    for row in rows:
        sensitivity = row.get("sensitivity", "normal").strip().lower()
        row["sensitivity"] = {
            "low": "normal",
            "medium": "sensitive",
            "high": "sensitive",
            "eeo": "sensitive",
        }.get(sensitivity, sensitivity)
    with (destination / "answers.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    if not (source / "profile.json").exists():
        raw = yaml_block((source / "profile.md").read_text(encoding="utf-8-sig"))
        prefs = raw.get("location_preferences", {})
        mapped = {k: v for k, v in raw.items() if k in Profile.model_fields}
        mapped.update(
            location=raw.get("city"),
            sponsorship=raw.get("requires_sponsorship"),
            relocation=prefs.get("willing_to_relocate"),
            location_prefs=prefs.get("preferred_cities", []),
        )
        profile = Profile.model_validate(mapped)
        (destination / "profile.json").write_text(
            profile.model_dump_json(), encoding="utf-8"
        )
        # Name components/country are explicit source facts, not model inferences.
        with (destination / "answers.csv").open(
            "a", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.writer(stream)
            for key in ("first_name", "last_name", "country"):
                if key in raw:
                    writer.writerow([key, raw[key], "normal", "profile.md", ""])
    text = (source / "rules.md").read_text(encoding="utf-8-sig")
    raw_rules = yaml_block(text)
    if raw_rules.get("eeo_policy") == "decline_to_answer":
        raw_rules["eeo_policy"] = "decline"
    rules = Rules.model_validate({**raw_rules, "prose": text})
    (destination / "rules.md").write_text(
        "---\n" + yaml.safe_dump(rules.model_dump(exclude={"prose"})) + "---\n" + text,
        encoding="utf-8",
    )
    return hashes


class RealData:
    """Delegate typed parsing to the data port after explicit format normalization."""

    def __init__(
        self,
        source: object,
        directory: Path,
        allowlist: DomainAllowlist,
        *,
        include_public: bool = False,
    ) -> None:
        self.source = source
        self.directory = directory
        self.allowlist = allowlist
        self.include_public = include_public
        self.loaded: DataSnapshot | None = None
        self.source_used = ""

    async def load(self, run_id: str) -> DataSnapshot:
        """Snapshot candidate files and fixture posting text before Gemini ranking."""
        if isinstance(self.source, DrivePublicDataSource):
            cache = self.directory / "drive"
            synced = self.source._sync_drive_files(cache)
            # Older port names a profile Doc profile.md. Never silently use partial Drive data.
            if synced and all(
                (cache / name).exists()
                for name in ("profile.md", "rules.md", "answers.csv", "resume.pdf")
            ):
                source = cache
                self.source_used = "drive_public"
                if not (cache / "job_queue.csv").exists():
                    shutil.copy2(
                        self.source.fallback_dir / "job_queue.csv",
                        cache / "job_queue.csv",
                    )
                    self.source_used += "+local_job_queue"
            else:
                if self.source.fallback_dir is None:
                    raise ValueError(
                        "Drive candidate files incomplete and fallback disabled"
                    )
                source = self.source.fallback_dir
                self.source_used = "local_fallback (Drive candidate files incomplete)"
        else:
            source = self.source.data_dir
            self.source_used = "local_folder"
        stage = self.directory / "normalized"
        original_hashes = normalize_source(source, stage)
        typed = await LocalFolderDataSource(stage).load(run_id)
        data = DataSnapshot.model_validate(typed.model_dump(mode="json"))
        public_queue = source / "job_queue_real.csv"
        if (
            not public_queue.exists()
            and isinstance(self.source, DrivePublicDataSource)
            and self.source.fallback_dir is not None
        ):
            public_queue = self.source.fallback_dir / "job_queue_real.csv"
        if self.include_public and public_queue.exists():
            from src.operator.data.local import parse_job_queue_csv

            original_hashes["job_queue_real.csv"] = hashlib.sha256(
                public_queue.read_bytes()
            ).hexdigest()
            for job in parse_job_queue_csv(public_queue):
                job.job_id = "public-" + job.job_id
                data.jobs.append(job)
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            for job in data.jobs:
                parts = urlsplit(job.url)
                if (
                    parts.hostname not in {"127.0.0.1", "localhost"}
                    or parts.port != 8780
                ):
                    continue
                host = os.environ.get("REAL_FIXTURE_HOST", "127.0.0.1")
                job.url = job.url.replace(parts.netloc, f"{host}:8780", 1)
                response = client.get(job.url)
                response.raise_for_status()
                parsed = PostingHTML(response.text)
                job.description = "\n".join(parsed.text)
                if len(parsed.apply_links) == 1:
                    job.apply_url = urljoin(job.url, parsed.apply_links[0])
                salaries = re.findall(r"INR\s+([\d,]+)", job.description)
                if salaries:
                    job.salary = float(salaries[0].replace(",", ""))
                job.remote = (
                    "remote" in job.description.casefold()
                    and "hybrid" not in job.description.casefold()
                )
        data.file_hashes.update({"source/" + k: v for k, v in original_hashes.items()})
        data.file_hashes["postings.json"] = hashlib.sha256(
            json.dumps(
                [j.model_dump(mode="json") for j in data.jobs], sort_keys=True
            ).encode()
        ).hexdigest()
        data.snapshot_hash = hashlib.sha256(
            json.dumps(data.file_hashes, sort_keys=True).encode()
        ).hexdigest()
        # Navigation grants exact queue hosts only; submission grants stay fixed at loopback:8780.
        updated = DomainAllowlist.from_urls([j.apply_url or j.url for j in data.jobs])
        object.__setattr__(
            self.allowlist, "hosts", self.allowlist.hosts | updated.hosts
        )
        self.loaded = data
        (self.directory / "data-manifest.json").write_text(
            json.dumps(
                {
                    "source": self.source_used,
                    "snapshot_hash": data.snapshot_hash,
                    "file_hashes": data.file_hashes,
                    "jobs": [j.model_dump(mode="json") for j in data.jobs],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return data
