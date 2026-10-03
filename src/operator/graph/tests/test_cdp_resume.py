"""Opt-in real Chrome, abrupt process-exit and server counter probe."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


@pytest.mark.skipif(
    not os.getenv("HULCHUL_BROWSER_SOURCE"),
    reason="requires explicit peer browser source and system Chrome",
)
@pytest.mark.parametrize(
    "crash, expected, posts",
    [
        ("crash_fill", "READY_FOR_REVIEW", 0),
        ("crash_approval", "SUBMITTED_VERIFIED", 1),
        ("crash_before", "SUBMITTED_UNVERIFIED", 0),
        ("crash_after", "SUBMITTED_VERIFIED", 1),
    ],
)
def test_core_cdp_and_ledger_across_abrupt_process_exit(
    tmp_path, crash, expected, posts
):
    source = Path(os.environ["HULCHUL_BROWSER_SOURCE"]).resolve()
    chrome_path = Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")
    assert chrome_path.exists()
    assert (source / "src/operator/browser/cdp.py").exists()
    counter = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if self.path == "/confirmation":
                body = "<h1>Application submitted successfully. Thank you for applying.</h1>"
            else:
                body = """<h1>Local synthetic ATS fixture</h1><form action='/submit' method='post'>
                <label for='name'>Full name</label><input id='name' name='name' required>
                <label for='reference'>Optional reference</label><input id='reference' name='reference'>
                <label for='note'>Optional note</label><input id='note' name='note'>
                <button type='submit'>Submit application</button></form><script>
                document.querySelector('input').addEventListener('input',()=>sessionStorage.setItem('fills',
                  Number(sessionStorage.getItem('fills')||0)+1));</script>"""
            self.wfile.write(body.encode())

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            counter.append(1)
            self.send_response(303)
            self.send_header("Location", "/confirmation")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    url = f"http://127.0.0.1:{server.server_port}/form"
    chrome = subprocess.Popen(
        [
            str(chrome_path),
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={tmp_path / 'chrome'}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        for attempt in range(100):
            try:
                with urlopen(endpoint + "/json/version", timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("system Chrome CDP did not start")
        base = [
            sys.executable,
            "-m",
            "src.operator.graph.tests.cdp_driver",
            "--source-root",
            str(source),
            "--state-dir",
            str(tmp_path),
            "--endpoint",
            endpoint,
            "--url",
            url,
        ]

        def run(mode):
            return subprocess.run(
                base + ["--mode", mode],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )

        if crash != "crash_fill":
            first = run("start")
            assert first.returncode == 0, first.stderr
            assert json.loads(first.stdout)["fills"] == 1
        failed = run(crash)
        assert failed.returncode in {71, 72, 73, 74}, failed.stderr
        resumed = run("resume")
        assert resumed.returncode == 0, resumed.stderr
        result = json.loads(resumed.stdout)
        assert result["status"] == expected
        assert result["fills"] == 1
        assert result["llm_calls_this_process"] == 0
        assert len(counter) == posts
        (tmp_path / "result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome.kill()
            chrome.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
