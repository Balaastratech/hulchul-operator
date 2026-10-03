"""Tiny threaded fixture server (stdlib only).

Start:  python -m fixtures.server [--port 8780]      (env FIXTURE_PORT also works)

Serves two ATS form layouts, a confirmation page, a hostile job board and a
server-side submission counter under /__test/.  Only POST /ats_*/submit and
POST /__test/reset ever change state; every GET is read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8780
MAX_BODY = 10 * 1024 * 1024
LAYOUTS = ("ats_a", "ats_b")

# Server-side validation rules per layout (field names differ on purpose).
RULES: dict[str, dict[str, Any]] = {
    "ats_a": {
        "required": {
            "first_name": "First name",
            "last_name": "Last name",
            "email": "Email",
            "phone": "Phone",
            "country": "Country",
            "work_authorization": "Work authorization",
            "requires_sponsorship": "Sponsorship question",
            "privacy_consent": "Privacy consent",
        },
        "email_field": "email",
        "resume_field": "resume",
    },
    "ats_b": {
        "required": {
            "given_name": "Given name",
            "family_name": "Family name",
            "email_address": "Email address",
            "mobile": "Mobile number",
            "work_eligibility": "Work eligibility",
            "consent_terms": "Terms consent",
        },
        "email_field": "email_address",
        "resume_field": "resume_upload",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_state() -> dict[str, Any]:
    return {
        "total": 0,
        "by_layout": {k: 0 for k in LAYOUTS},
        "duplicate_posts": 0,
        "items": [],
    }


class Store:
    """In-memory submission counter, persisted to a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "items" in data:
                base = _empty_state()
                base.update(data)
                return base
        except (OSError, ValueError):
            pass
        return _empty_state()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def has(self, app_id: str) -> dict[str, Any] | None:
        with self.lock:
            for item in self.state["items"]:
                if item["id"] == app_id:
                    return dict(item)
        return None

    def accept(self, layout: str, fields: dict[str, Any], resume_sha: str | None,
               files: dict[str, dict[str, Any]]) -> dict[str, Any]:
        canonical = json.dumps({"layout": layout, "fields": fields, "files": files},
                               sort_keys=True)
        payload_sha = hashlib.sha256(canonical.encode()).hexdigest()
        with self.lock:
            dup_of = next((i["id"] for i in self.state["items"]
                           if i["payload_sha256"] == payload_sha), None)
            item = {
                "id": "APP-" + uuid.uuid4().hex[:8],
                "layout": layout,
                "received_at": _now(),
                "fields": fields,
                "resume_sha256": resume_sha,
                "files": files,
                "payload_sha256": payload_sha,
                "duplicate_of": dup_of,
            }
            self.state["items"].append(item)
            self.state["total"] += 1
            self.state["by_layout"][layout] += 1
            if dup_of:
                self.state["duplicate_posts"] += 1
            self._persist()
            return item

    def reset(self) -> None:
        with self.lock:
            self.state = _empty_state()
            self._persist()


# ---------------------------------------------------------------- parsing

def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Parse multipart/form-data -> (text fields, files). Repeated names become lists."""
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        raise ValueError("missing boundary")
    delim = b"--" + m.group(1).encode()
    fields: dict[str, Any] = {}
    files: dict[str, dict[str, Any]] = {}
    for raw in (b"\r\n" + body).split(b"\r\n" + delim)[1:]:
        if raw.startswith(b"--"):
            break
        head, sep, data = raw.lstrip(b"\r\n").partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = head.decode("utf-8", "replace")
        name_m = re.search(r'name="([^"]*)"', headers)
        if not name_m:
            continue
        name = name_m.group(1)
        file_m = re.search(r'filename="([^"]*)"', headers)
        if file_m is not None:
            if file_m.group(1) or data:
                files[name] = {
                    "filename": file_m.group(1),
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            continue
        _add(fields, name, data.decode("utf-8", "replace"))
    return fields, files


def parse_urlencoded(body: bytes) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, values in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items():
        for v in values:
            _add(fields, key, v)
    return fields


def _add(fields: dict[str, Any], name: str, value: str) -> None:
    if name in fields:
        if not isinstance(fields[name], list):
            fields[name] = [fields[name]]
        fields[name].append(value)
    else:
        fields[name] = value


def validate(layout: str, fields: dict[str, Any], files: dict[str, dict[str, Any]]) -> list[str]:
    rules = RULES[layout]
    errors: list[str] = []
    for name, label in rules["required"].items():
        value = fields.get(name)
        if isinstance(value, list):
            value = "".join(value)
        if value is None or not str(value).strip():
            errors.append(f"{label} is required.")
    email = str(fields.get(rules["email_field"], "")).strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        errors.append("Email is not a valid address.")
    resume = files.get(rules["resume_field"])
    if not resume or not resume["size"]:
        errors.append("Resume file is required.")
    return errors


# ---------------------------------------------------------------- handler

class Handler(BaseHTTPRequestHandler):
    server_version = "FixtureServer/1.0"
    timeout = 30  # a hung client cannot hold a worker thread forever
    store: Store  # set on the server class

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
        if os.environ.get("FIXTURE_VERBOSE"):
            super().log_message(fmt, *args)

    # -- helpers
    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8",
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: Any) -> None:
        self._send(status, json.dumps(obj, indent=2).encode(), "application/json")

    def _text_page(self, status: int, title: str, message: str) -> None:
        page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(title)}"
                f"</title></head><body><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body></html>")
        self._send(status, page.encode())

    def _file(self, rel: str, errors: list[str] | None = None) -> None:
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            self._text_page(404, "Not found", "No such page.")
            return
        if errors:
            items = "".join(f"<li>{html.escape(e)}</li>" for e in errors)
            banner = ('<div class="error-summary" role="alert"><strong>We could not submit your '
                      f"application. Please fix the following:</strong><ul>{items}</ul></div>")
        else:
            banner = ""
        self._send(200, text.replace("<!--@ERRORS@-->", banner).encode())

    # -- GET (read-only)
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/":
            self._file("index.html")
        elif path in ("/ats_a", "/ats_b", "/login_wall", "/captcha_stub"):
            self._send(301, b"", extra={"Location": path + "/"})
        elif path == "/ats_a/":
            self._file("ats_a/index.html")
        elif path == "/ats_b/":
            self._file("ats_b/index.html")
        elif path == "/login_wall/":
            self._file("login_wall/index.html")
        elif path == "/captcha_stub/":
            self._file("captcha_stub/index.html")
        elif path == "/captcha_stub/challenge.html":
            self._file("captcha_stub/challenge.html")
        elif path == "/jobs":
            self._send(301, b"", extra={"Location": "/jobs/"})
        elif path == "/jobs/":
            self._file("job_board_hostile/index.html")
        elif (m := re.fullmatch(r"/jobs/(job-\d{4})", path)):
            self._file(f"job_board_hostile/{m.group(1)}.html")
        elif (m := re.fullmatch(r"/confirmation/(APP-[0-9a-f]{8})", path)):
            item = self.store.has(m.group(1))
            if not item:
                self._text_page(404, "Not found", "Unknown application.")
            else:
                self._confirmation(item)
        elif path == "/__test/health":
            self._json(200, {"ok": True})
        elif path == "/__test/submissions":
            self._json(200, self.store.snapshot())
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._text_page(404, "Not found", "No such page.")

    def _confirmation(self, item: dict[str, Any]) -> None:
        app_id = html.escape(item["id"])
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Application received</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#222}}
.ok{{border:1px solid #2e7d32;background:#f1f8f1;padding:1.5rem;border-radius:8px}}</style></head>
<body><main class="ok"><h1>Thank you, your application has been submitted</h1>
<p>Your application id is <strong id="application-id">{app_id}</strong>.</p>
<p>Keep this id for your records. You may close this window.</p></main></body></html>"""
        self._send(200, page.encode())

    # -- POST (the only mutating verbs)
    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/__test/reset":
            self._drain()
            self.store.reset()
            self._json(200, {"ok": True})
            return
        m = re.fullmatch(r"/(ats_a|ats_b)/submit", path)
        if not m:
            self._drain()
            self._text_page(404, "Not found", "No such endpoint.")
            return
        layout = m.group(1)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._text_page(413, "Too large", "Request body too large.")
            return
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        try:
            if ctype.startswith("multipart/form-data"):
                fields, files = parse_multipart(ctype, body)
            else:
                fields, files = parse_urlencoded(body), {}
        except ValueError:
            self._text_page(400, "Bad request", "Malformed form data.")
            return
        errors = validate(layout, fields, files)
        if errors:
            self._file(f"{layout}/index.html", errors)  # re-render, not counted
            return
        resume = files.get(RULES[layout]["resume_field"], {})
        item = self.store.accept(layout, fields, resume.get("sha256"), files)
        self._send(303, b"", extra={"Location": f"/confirmation/{item['id']}"})

    def _drain(self) -> None:
        try:
            n = min(int(self.headers.get("Content-Length", "0")), MAX_BODY)
        except ValueError:
            n = 0
        if n > 0:
            self.rfile.read(n)

    def _not_allowed(self) -> None:
        self._text_page(405, "Method not allowed", "This method is not supported here.")

    do_PUT = do_DELETE = do_PATCH = _not_allowed  # type: ignore[assignment]


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(port: int = DEFAULT_PORT, state_dir: Path | None = None) -> FixtureServer:
    """Build (but do not start) a server bound to 127.0.0.1. port=0 picks a free port."""
    state_dir = state_dir or Path(os.environ.get("FIXTURE_STATE_DIR", ROOT / ".state"))
    handler = type("BoundHandler", (Handler,), {"store": Store(Path(state_dir) / "submissions.json")})
    return FixtureServer(("127.0.0.1", port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hulchul local fixture server")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("FIXTURE_PORT", DEFAULT_PORT)))
    args = parser.parse_args()
    srv = make_server(args.port)
    print(f"Fixture server on http://127.0.0.1:{srv.server_address[1]}/  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
