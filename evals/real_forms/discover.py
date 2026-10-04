"""Refresh public job feed targets using GET only; no credentials or HTML scraping."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
import yaml

FEEDS = [
    ("greenhouse", "Vercel", "https://boards-api.greenhouse.io/v1/boards/vercel/jobs"),
    ("lever", "Palantir", "https://api.lever.co/v0/postings/palantir?mode=json"),
    ("ashby", "Ashby", "https://api.ashbyhq.com/posting-api/job-board/ashby"),
    ("workable", "Apna", "https://apply.workable.com/api/v1/widget/accounts/apna"),
    ("breezy", "Carnegie Robotics", "https://carnegie-robotics.breezy.hr/json"),
    (
        "smartrecruiters",
        "Expeditors",
        "https://api.smartrecruiters.com/v1/companies/Expeditors/postings",
    ),
    ("recruitee", "bunq", "https://bunq.recruitee.com/api/offers/"),
    ("personio", "Personio", "https://personio.jobs.personio.de/xml?language=en"),
    ("teamtailor", "Teamtailor", "https://career.teamtailor.com/jobs.rss"),
    ("bamboohr", "Whitman College", "https://whitman.bamboohr.com/careers/list"),
]


def refresh(path: Path) -> None:
    """Select one current posting per feed and save public source provenance."""
    targets = []
    for site, company, api in FEEDS:
        response = requests.get(api, timeout=30)
        response.raise_for_status()
        if site == "personio":
            jobs = [
                {"id": e.findtext("id"), "title": e.findtext("name")}
                for e in ET.fromstring(response.content).findall("position")
            ]
        elif site == "teamtailor":
            jobs = [
                {"url": e.findtext("link"), "title": e.findtext("title")}
                for e in ET.fromstring(response.content).findall("channel/item")
            ]
        else:
            data = response.json()
            if site.startswith("greenhouse") or site == "ashby" or site == "workable":
                jobs = data["jobs"]
            elif site == "smartrecruiters":
                jobs = data["content"]
            elif site == "recruitee":
                jobs = data["offers"]
            elif site == "bamboohr":
                jobs = [{**j, "title": j["jobOpeningName"]} for j in data["result"]]
            else:
                jobs = data
        if not jobs:
            raise ValueError(f"No public postings in {site}")
        job = next(
            (
                j
                for j in jobs
                if any(
                    s in str(j.get("title", j.get("name", j.get("text", "")))).lower()
                    for s in ("software", "backend", "engineer")
                )
            ),
            jobs[0],
        )
        title = job.get("title", job.get("name", job.get("text", "Public posting")))
        if site.startswith("greenhouse"):
            url = job["absolute_url"]
        elif site == "lever" or site == "ashby":
            url = job["applyUrl"]
        elif site == "workable":
            url = job.get("application_url") or job["url"].rstrip("/") + "/apply/"
        elif site == "breezy":
            url = job["url"].rstrip("/") + "/apply"
        elif site == "smartrecruiters":
            detail = requests.get(job["ref"], timeout=30)
            detail.raise_for_status()
            url = detail.json()["applyUrl"]
        elif site == "recruitee":
            url = job["careers_url"].rstrip("/") + "/new"
        elif site == "personio":
            url = f"https://personio.jobs.personio.de/job/{job['id']}?language=en&display=en#apply"
        elif site == "bamboohr":
            url = f"https://whitman.bamboohr.com/careers/{job['id']}"
        else:
            url = job["url"].rstrip("/") + "/applications/new"
        targets.append(
            {
                "id": site,
                "ats": site.split("_second")[0],
                "company": company,
                "title": title,
                "url": url,
                "source_api": api,
                "discovered_at": datetime.now(UTC).isoformat(),
                "listing_count": len(jobs),
            }
        )
        print(f"Discovered {site}: {len(jobs)} public postings")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"targets": targets}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
