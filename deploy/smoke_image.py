"""Run the built image with ephemeral credentials and verify its health and UID."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import time


def docker(*args: str, env=None) -> str:
    """Return only requested Docker output; never inspect environment values."""
    return subprocess.check_output(["docker", *args], env=env, text=True).strip()


def main() -> None:
    """Verify health, persistent volume and non-root identity, then clean up."""
    suffix = secrets.token_hex(4)
    name, volume = "hulchul-cp-smoke-" + suffix, "hulchul-cp-smoke-data-" + suffix
    environment = dict(os.environ, CP_SIGNING_KEY=secrets.token_urlsafe(48),
                       CP_WORKER_TOKEN=secrets.token_urlsafe(48), CP_BASE_URL="https://smoke.example.test")
    docker("volume", "create", volume)
    try:
        docker("run", "-d", "--name", name, "-e", "CP_SIGNING_KEY", "-e", "CP_WORKER_TOKEN",
               "-e", "CP_BASE_URL", "-v", volume + ":/data", "hulchul-control-plane:t033", env=environment)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = json.loads(docker("inspect", "--format", "{{json .State}}", name))
            if state["Status"] == "exited":
                raise RuntimeError("control plane container exited; inspect redacted startup diagnostics")
            if state.get("Health", {}).get("Status") == "healthy":
                break
            time.sleep(1)
        else:
            raise RuntimeError("container healthcheck timed out")
        assert docker("exec", name, "id", "-u") == "10001"
        assert docker("exec", name, "id", "-g") == "10001"
        probe = docker("exec", name, "python", "-c",
                       "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8790/healthz'); print(r.status, r.read().decode())")
        assert probe == '200 {"status":"ok"}'
        assert docker("exec", name, "python", "-c",
                      "from pathlib import Path; print(Path('/data/control_plane.sqlite').is_file())") == "True"
        docker("exec", name, "python", "-c",
               "import sqlite3; c=sqlite3.connect('/data/control_plane.sqlite'); c.execute(\"INSERT INTO kv(k,v) VALUES ('smoke-proof','persisted')\"); c.commit()")
        docker("restart", name)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if docker("inspect", "--format", "{{.State.Health.Status}}", name) == "healthy":
                break
            time.sleep(1)
        else:
            raise RuntimeError("container health after restart timed out")
        assert docker("exec", name, "python", "-c",
                      "import sqlite3; c=sqlite3.connect('/data/control_plane.sqlite'); print(c.execute(\"SELECT v FROM kv WHERE k='smoke-proof'\").fetchone()[0])") == "persisted"
        print("Docker PASS: /healthz=200; healthy; UID/GID=10001; SQLite persisted in named volume; restart succeeded")
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "volume", "rm", volume], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
