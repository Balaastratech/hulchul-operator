"""Render saved graph state, JSONL milestones and SQLite ledger as inert HTML."""

from __future__ import annotations

import base64
import html
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SECRET = re.compile(
    r"(?:token(?!s|count)|secret|password|credential|authorization|api[_-]?key|signing[_-]?key|cookie)",
    re.IGNORECASE,
)
_OPAQUE = re.compile(
    r"\b(?:[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]+)?|[A-Za-z0-9_+/=-]{48,})\b"
)
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_ASSIGN = re.compile(
    r"\b(?:[\w-]*(?:token|secret|password|api_key|signing_key)[\w-]*)\s*[=:]\s*[^\s,;]+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[^\s<>\"']+", re.IGNORECASE)
_TABLES = ("applications", "actions", "approvals", "events")
_LABELS = {
    "E01": "Run started",
    "E02": "Shortlist",
    "E03": "Injection quarantined",
    "E04": "Login handoff",
    "E05": "Human check",
    "E06": "Answer needed",
    "E07": "Review gate",
    "E08": "Edit applied",
    "E09": "Submitting",
    "E10": "Submission verified",
    "E11": "Submission uncertain",
    "E12": "Blocked",
    "E13": "Pause / resume",
    "E14": "Run summary",
    "E15": "Worker offline",
}


def _secret_values(value: Any, key: str = "") -> set[str]:
    """Suppress known credentials even if echoed into an unstructured message."""
    if isinstance(value, str) and key.endswith("_json"):
        try:
            return _secret_values(json.loads(value))
        except ValueError:
            return set()
    if isinstance(value, str) and _SECRET.search(key) and len(value) >= 4:
        return {value}
    if isinstance(value, dict):
        result = set()
        field = str(value.get("field_key", ""))
        for k, v in value.items():
            result.update(
                _secret_values(
                    v,
                    "password"
                    if _SECRET.search(field) and k in {"actual", "intended", "value"}
                    else str(k),
                )
            )
        return result
    if isinstance(value, (list, tuple)):
        return set().union(*(_secret_values(v) for v in value)) if value else set()
    return set()


def _clean(value: Any, key: str = "") -> Any:
    if _SECRET.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        field = str(value.get("field_key", value.get("Field", "")))
        if _SECRET.search(field):
            value = {
                k: "[redacted]" if k.lower() in {"intended", "actual", "value"} else v
                for k, v in value.items()
            }
        return {str(k): _clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        # Capability URLs are kept as plain origins/paths with query removed.
        value = _URL.sub(
            lambda m: (
                m[0].split("?", 1)[0].split("#", 1)[0]
                if "@" not in m[0]
                else "[redacted URL]"
            ),
            value,
        )
        value = _BEARER.sub("Bearer [redacted]", value)
        value = _ASSIGN.sub("[redacted credential]", value)
        if "hash" in key or key == "sha256":
            return value[:12] + "…" if len(value) > 12 else value
        return _OPAQUE.sub("[redacted opaque value]", value)
    return value


def _e(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        value = json.dumps(_clean(value), ensure_ascii=False, indent=2)
    else:
        value = str(_clean(value))
    return html.escape(value, quote=True)


def _time(value: Any) -> float | None:
    try:
        if isinstance(value, (int, float)):
            return float(value)
        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _when(value: Any) -> str:
    stamp = _time(value)
    if stamp is None:
        return "not recorded"
    return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="seconds")


def _database(path: Path, run_id: str | None) -> dict[str, list[dict]]:
    result = {}
    # mode=ro also prevents accidental creation of an empty ledger.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        names = {
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in (*_TABLES, "snapshots", "runs", "evidence"):
            if table not in names:
                continue
            columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
            if "run_id" not in columns:
                continue
            query = f'SELECT * FROM "{table}"'
            rows = db.execute(
                query + (" WHERE run_id=?" if run_id else ""),
                (run_id,) if run_id else (),
            )
            result[table] = [dict(r) for r in rows]
    return result


def _files(source: Path) -> list[Path]:
    # Explicit artifact names only; never read .env, profiles or credentials.
    result = []
    for name in (
        "state.json",
        "events.jsonl",
        "ledger.sqlite",
        "control-plane.sqlite",
        "control_plane.sqlite",
        "cp.sqlite",
    ):
        result.extend(
            p
            for p in source.rglob(name)
            if not any(part.startswith(".") for part in p.relative_to(source).parts)
        )
    return result


def _load(source: Path, run_id: str | None) -> dict:
    source = source.resolve()
    files = _files(source)
    if not files:
        raise ValueError("no saved artifacts")
    if run_id is None:
        candidates = []
        saved_states = [p for p in files if p.name == "state.json"]
        # A state artifact is the authoritative latest saved checkpoint. Shared
        # database mtimes cannot identify which of several runs changed last.
        latest_files = saved_states or files
        for path in latest_files:
            if path.name == "state.json":
                state = json.loads(path.read_text(encoding="utf-8-sig"))
                if state.get("run_id"):
                    candidates.append((path.stat().st_mtime, state["run_id"]))
            elif path.suffix == ".sqlite":
                undated_ids = set()
                for rows in _database(path, None).values():
                    for row in rows:
                        stamp = next(
                            (
                                _time(row.get(key))
                                for key in (
                                    "created_at",
                                    "approved_at",
                                    "used_at",
                                    "received_at",
                                )
                                if _time(row.get(key)) is not None
                            ),
                            None,
                        )
                        if stamp is not None:
                            candidates.append((stamp, row["run_id"]))
                        else:
                            undated_ids.add(row["run_id"])
                if not candidates and len(undated_ids) == 1:
                    candidates.append((path.stat().st_mtime, next(iter(undated_ids))))
            elif path.name == "events.jsonl":
                for line in path.read_text(encoding="utf-8-sig").splitlines():
                    event = json.loads(line)
                    if event.get("run_id"):
                        candidates.append(
                            (
                                _time(event.get("created_at")) or path.stat().st_mtime,
                                event["run_id"],
                            )
                        )
        if not candidates:
            raise ValueError("no runs")
        run_id = max(candidates)[1]
    state, events, ledgers, snapshots, roots = {}, [], [], [], set()
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        if path.name == "state.json":
            item = json.loads(path.read_text(encoding="utf-8-sig"))
            if item.get("run_id") == run_id:
                state = item
                roots.add(path.parent)
        elif path.name == "events.jsonl":
            items = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8-sig").splitlines()
                if line.strip()
            ]
            selected = [v for v in items if v.get("run_id") == run_id]
            events.extend(selected)
            if selected:
                roots.add(path.parent)
        else:
            tables = _database(path, run_id)
            if any(tables.values()):
                ledgers.append((path.name, tables))
                roots.add(path.parent)
                snapshots.extend(
                    json.loads(r["body_json"]) for r in tables.get("snapshots", [])
                )
                for row in tables.get("events", []):
                    if row.get("body_json"):
                        events.append(json.loads(row["body_json"]))
                    elif not any(
                        e.get("event_id") == row.get("event_id")
                        and e.get("message") == row.get("message")
                        for e in events
                    ):
                        events.append(row)
    unique = {json.dumps(v, sort_keys=True): v for v in events}
    events = sorted(unique.values(), key=lambda v: _time(v.get("created_at")) or 0)
    if not state and not events and not ledgers:
        raise ValueError("run not found")
    return {
        "run_id": run_id,
        "state": state,
        "events": events,
        "ledgers": ledgers,
        "snapshots": snapshots,
        "roots": roots,
    }


def _picture(value: Any, roots: set[Path]) -> str:
    if not isinstance(value, str):
        return '<p class="muted">Screenshot reference unavailable.</p>'
    path = Path(value)
    candidates = [path] if path.is_absolute() else [root / path for root in roots]
    for candidate in candidates:
        candidate = candidate.resolve()
        if not any(candidate.is_relative_to(root.resolve()) for root in roots):
            continue
        if not candidate.is_file() or candidate.stat().st_size > 12 * 1024 * 1024:
            continue
        data = candidate.read_bytes()
        mime = (
            "image/png"
            if data.startswith(b"\x89PNG\r\n\x1a\n")
            else "image/jpeg"
            if data.startswith(b"\xff\xd8\xff")
            else "image/webp"
            if data[:4] == b"RIFF" and data[8:12] == b"WEBP"
            else None
        )
        if mime:
            encoded = base64.b64encode(data).decode("ascii")
            return f'<figure><img alt="Recorded run screenshot" loading="lazy" src="data:{mime};base64,{encoded}"><figcaption>Recorded local evidence</figcaption></figure>'
    return '<p class="muted">Screenshot missing or outside the saved run directory.</p>'


def _table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="muted">No records.</p>'
    rows = [_clean(row) for row in rows]
    keys = list(dict.fromkeys(k for row in rows for k in row))
    header = "".join(f"<th>{_e(k)}</th>" for k in keys)
    body = "".join(
        "<tr>"
        + "".join(f"<td><pre>{_e(_clean(row.get(k), k))}</pre></td>" for k in keys)
        + "</tr>"
        for row in rows
    )
    return f'<div class="scroll"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def _review(snapshot: dict) -> str:
    generated = snapshot.get("generated_texts", {})
    unanswered = snapshot.get("unanswered", [])
    rows = []
    for field in snapshot.get("fields", []):
        field = _clean(field)
        key = field.get("field_key", "?")
        flags = []
        if field.get("generated") or key in generated:
            flags.append("GENERATED TEXT")
        if key in unanswered or field.get("escalated"):
            flags.append("NEEDS ANSWER")
        rows.append(
            {
                "Field": key,
                "Intended": field.get("intended"),
                "Actual": field.get("actual"),
                "Read-back": "MATCH"
                if field.get("matched")
                else "MISMATCH / unresolved",
                "Flags": ", ".join(flags),
                "Reason": field.get("reason"),
            }
        )
    return (
        _table(rows)
        + "<h4>Unanswered fields</h4>"
        + _table([{"Field": key} for key in unanswered])
        + "<h4>Generated drafts</h4>"
        + _table([{"Field": k, "Text (generated)": v} for k, v in generated.items()])
        + "<h4>Uploads</h4>"
        + _table(snapshot.get("uploads", []))
    )


_CSS = """
:root{color-scheme:light;font-family:system-ui,Segoe UI,sans-serif;color:#172b3a;background:#edf2f5}*{box-sizing:border-box}body{margin:0}main{max-width:1200px;margin:auto;padding:32px}h1{font-size:34px;margin:6px 0 12px}h2{font-size:24px}h3{margin:0 0 10px}h4{margin:18px 0 8px}p{line-height:1.6}.eyebrow{color:#126d77;font-size:13px;font-weight:750;letter-spacing:2px}.muted,small,figcaption{color:#536877}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card,section,article{background:white;border:1px solid #d6e0e6;border-radius:12px;padding:22px;margin:16px 0}.card strong{display:block;font-size:24px}.card{margin:0}.badge{display:inline-block;background:#def3e8;color:#17533b;border-radius:6px;padding:6px 10px;font-weight:700;font-size:13px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:12px;border-bottom:1px solid #dfe7ec;text-align:left;vertical-align:top}th{background:#eff5f7}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;margin:0;max-width:600px}img{display:block;max-width:100%;max-height:600px;object-fit:contain;object-position:left;border:1px solid #ccd8df}figure{margin:16px 0}figcaption{font-size:12px;margin-top:6px}details{margin:14px 0}summary{cursor:pointer;color:#126d77;font-weight:650}.step{border-left:4px solid #167d87}.step .number{color:#167d87}nav{display:flex;gap:18px;flex-wrap:wrap;margin:24px 0}a{color:#126d77}.warning{background:#fff3d8;border-left:4px solid #ad7400;padding:12px}@media(max-width:760px){main{padding:16px}.cards{grid-template-columns:repeat(2,1fr)}}@media print{details{display:block}img{max-height:400px}section,article{break-inside:avoid}}
"""


def write_report(source: Path, run_id: str | None, output: Path) -> Path:
    """Write a self-contained report; None selects the most recently saved run.

    Source contains saved state.json/events.jsonl/ledger.sqlite, optionally a
    control-plane database. Screenshots must live beneath an artifact directory.
    Evidence pixels are trusted local captures: use synthetic runs for sharing.
    """
    data = _load(Path(source), run_id)
    state, events = data["state"], data["events"]
    roots = data["roots"]
    jobs = state.get("jobs", {})
    usage = state.get("usage", {})
    status = state.get("status", "not recorded")
    if not jobs:
        for _, tables in data["ledgers"]:
            for row in tables.get("applications", []):
                jobs[row["job_id"]] = {
                    "status": row.get("status"),
                    "notes": ["Read from ledger; graph state unavailable."],
                }
    end = next((e for e in reversed(events) if e.get("event_id") == "E14"), {})
    parts = [
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>Run report · {_e(data["run_id"])}</title><style>{_CSS}</style></head><body><main>',
        f'<header><div class="eyebrow">HULCHUL / RUN TRACE</div><h1>Run {_e(data["run_id"])}</h1><span class="badge">{_e(status)}</span><p>{_e(state.get("goal", "Saved run trace"))}</p></header>',
        '<nav><a href="#timeline">Timeline</a><a href="#readback">Read-back</a><a href="#approval">Approval</a><a href="#ledger">Ledger</a><a href="#quarantine">Quarantines</a></nav>',
        '<div class="cards">'
        + "".join(
            f'<div class="card"><small>{label}</small><strong>{_e(value)}</strong></div>'
            for label, value in [
                ("Jobs", len(jobs)),
                ("LLM calls (recorded)", usage.get("llm_calls", "not recorded")),
                ("Tokens (recorded)", usage.get("tokens", "not recorded")),
                ("Cost INR (recorded)", usage.get("cost_inr", "not recorded")),
            ]
        )
        + "</div>",
        '<p class="muted">Offline report · UTC timestamps · credentials and capability links redacted. Usage is the saved graph counter; zero is not proof of metered zero cost. Step intervals are time between milestones, including human waits; execution durations appear only when recorded.</p>',
        "<section><h2>Final status & reasons</h2>"
        + _table(
            [
                {
                    "Job": k,
                    "Status": v.get("status"),
                    "Reasons / blockers": v.get("blockers", []),
                    "Notes": v.get("notes", []),
                }
                for k, v in jobs.items()
            ]
        )
        + f"<p>{_e(end.get('message', 'No terminal summary recorded; run may still be paused or incomplete.'))}</p><pre>{_e(end.get('payload', {}))}</pre></section>",
        '<section id="timeline"><h2>Step timeline</h2>',
    ]
    for index, event in enumerate(events):
        payload = event.get("payload", {})
        snapshot = payload.get("review_snapshot") or payload.get("review") or {}
        pictures = list(
            dict.fromkeys(
                snapshot.get("screenshots", [])
                + payload.get("evidence", [])
                + payload.get("screenshots", [])
            )
        )
        start = _time(event.get("created_at"))
        next_time = (
            _time(events[index + 1].get("created_at"))
            if index + 1 < len(events)
            else None
        )
        interval = (
            f"{max(0, next_time - start):.2f}s to next milestone"
            if start is not None and next_time is not None
            else "next interval not recorded"
        )
        duration = payload.get("duration_seconds", payload.get("duration_s"))
        duration_label = f" · execution {_e(duration)}s" if duration is not None else ""
        parts.append(
            f'<article class="step"><h3><span class="number">{index + 1:02d}</span> · {_e(_LABELS.get(event.get("event_id"), event.get("event_id", "Step")))}</h3><small>{_e(_when(event.get("created_at")))} · Job {_e(event.get("job_id"))} · {_e(interval)}{duration_label}</small><p>{_e(event.get("message"))}</p>'
        )
        parts.extend(_picture(p, roots) for p in pictures)
        if not pictures:
            parts.append(
                '<p class="muted">No screenshot recorded for this milestone.</p>'
            )
        if snapshot:
            parts.append(
                "<details><summary>Read-back at this step</summary>"
                + _review(snapshot)
                + "</details>"
            )
        parts.append(
            "<details><summary>Event payload</summary><pre>"
            + _e(event)
            + "</pre></details></article>"
        )
    parts.append('</section><section id="readback"><h2>Latest read-back per job</h2>')
    reviews = {
        k: v.get("review_snapshot") for k, v in jobs.items() if v.get("review_snapshot")
    }
    for event in reversed(events):
        snap = event.get("payload", {}).get("review_snapshot") or event.get(
            "payload", {}
        ).get("review")
        if snap and event.get("job_id") not in reviews:
            reviews[event.get("job_id")] = snap
    for snap in data["snapshots"]:
        reviews.setdefault(snap.get("job_id", "snapshot"), snap)
    for key, snapshot in reviews.items():
        parts.append(f"<h3>Job {_e(key)}</h3>" + _review(snapshot))
        parts.extend(_picture(p, roots) for p in snapshot.get("screenshots", []))
    if not reviews:
        parts.append('<p class="muted">Read-back not recorded.</p>')
    parts.append(
        '</section><section id="approval"><h2>Approval record</h2><p class="muted">The current schemas do not persist the approver identity. “Not recorded” does not identify a person. Worker used_at is consumption time; control-plane approved_at is click time.</p>'
    )
    approval_rows = []
    for name, tables in data["ledgers"]:
        for row in tables.get("approvals", []):
            approval_rows.append(
                {
                    "Source": name,
                    "Job": row.get("job_id"),
                    "Who": row.get("approved_by", "not recorded"),
                    "Click time": _when(row.get("approved_at")),
                    "Consumed": _when(row.get("used_at")),
                    "Snapshot hash prefix": _clean(
                        row.get("snapshot_hash"), "snapshot_hash"
                    ),
                    "State": row.get(
                        "status",
                        "revoked"
                        if row.get("revoked")
                        else "consumed"
                        if row.get("used_at")
                        else "registered",
                    ),
                }
            )
    parts.append(
        _table(approval_rows)
        + '</section><section id="quarantine"><h2>Injection quarantines</h2>'
    )
    parts.append(
        _table(
            [
                {
                    "Job": e.get("job_id"),
                    "Reason": e.get("message"),
                    "Details": e.get("payload"),
                }
                for e in events
                if e.get("event_id") == "E03"
            ]
        )
    )
    parts.append(
        _table(
            [
                {
                    "Job": job.get("job_id"),
                    "Reason": job.get("reasons", []),
                    "Quarantined": True,
                }
                for job in state.get("shortlist", [])
                if job.get("quarantined")
            ]
        )
    )
    parts.append(
        '</section><section id="ledger"><h2>Every ledger row for this run</h2><p class="muted">Read-only SQLite snapshots. All rows retained; sensitive columns redacted and hashes shortened.</p>'
    )
    for name, tables in data["ledgers"]:
        parts.append(f"<h3>{_e(name)}</h3>")
        for table, rows in tables.items():
            # Decode JSON columns so credential key redaction works recursively.
            decoded = []
            for row in rows:
                record = dict(row)
                for key, value in row.items():
                    if key.endswith("_json") and isinstance(value, str):
                        record[key] = json.loads(value)
                decoded.append(record)
            parts.append(f"<h4>{_e(table)} · {len(rows)} rows</h4>" + _table(decoded))
    parts.append(
        '</section><footer><p class="muted">Generated locally from saved evidence. No remote assets, scripts, forms or submission controls.</p></footer></main></body></html>'
    )
    output = Path(output).resolve()
    if output in _files(Path(source).resolve()):
        raise ValueError("output would overwrite input")
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = "".join(parts)
    for secret in sorted(_secret_values(data), key=len, reverse=True):
        rendered = rendered.replace(html.escape(secret, quote=True), "[redacted]")
    output.write_text(rendered, encoding="utf-8")
    return output
