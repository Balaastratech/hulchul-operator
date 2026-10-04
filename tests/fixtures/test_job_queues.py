"""Static checks on the two job queue CSVs . No network."""
from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
HEADER = ["url", "company", "title", "added_by"]


def _rows(name: str) -> tuple[list[str], list[dict[str, str]]]:
    with (REPO / "sample_data" / name).open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def test_local_queue_only_uses_fixture_port():
    header, rows = _rows("job_queue.csv")
    assert header == HEADER and rows
    for row in rows:
        parts = urlsplit(row["url"])
        assert parts.scheme == "http" and parts.netloc == "127.0.0.1:8780", row["url"]


def test_real_queue_has_three_https_urls_on_distinct_hosts():
    header, rows = _rows("job_queue_real.csv")
    assert header == HEADER
    assert len(rows) == 3
    hosts = []
    for row in rows:
        parts = urlsplit(row["url"])
        assert parts.scheme == "https" and parts.hostname, row["url"]
        assert row["added_by"] == "public-fill-only-proof"
        hosts.append(parts.hostname)
    assert len(set(hosts)) == 3


def test_real_urls_are_not_in_local_queue():
    _, local = _rows("job_queue.csv")
    _, real = _rows("job_queue_real.csv")
    assert not {r["url"] for r in local} & {r["url"] for r in real}
