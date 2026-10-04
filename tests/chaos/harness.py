"""Actual G3 subprocesses with a test-only kill barrier."""

import atexit
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Self

from fixtures.server import FixtureServer, Handler, Store
from scripts import demo_g3
from scripts.demo_g3 import ROOT, Scenario, wait_for

_fixture_server = None


class BorrowedFixture:
    """Keep one exclusive listener for the suite; reset only synthetic counter storage."""

    def __init__(self, server: FixtureServer) -> None:
        self.RequestHandlerClass = server.RequestHandlerClass

    def serve_forever(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def server_close(self) -> None:
        pass


def exclusive_fixture(port: int, state_dir: Path) -> BorrowedFixture:
    """Prevent Windows address reuse by peers, and avoid TIME_WAIT rebinding races."""
    global _fixture_server
    if _fixture_server is None:
        handler = type(
            "ChaosFixtureHandler",
            (Handler,),
            {"store": Store(state_dir / "submissions.json")},
        )
        server = FixtureServer(("127.0.0.1", port), handler, bind_and_activate=False)
        server.allow_reuse_address = False
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            server.server_bind()
            server.server_activate()
        except BaseException:
            server.server_close()
            raise
        _fixture_server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def close():
            server.shutdown()
            server.server_close()

        atexit.register(close)
    _fixture_server.RequestHandlerClass.store = Store(state_dir / "submissions.json")
    return BorrowedFixture(_fixture_server)


class ChaosScenario(Scenario):
    """Kill once at the selected boundary; subsequent workers restore SQLite/CDP."""

    node = ""
    phase = "before"
    variant = "normal"

    def __enter__(self) -> Self:
        original = demo_g3.make_server
        demo_g3.make_server = exclusive_fixture
        try:
            return super().__enter__()
        finally:
            demo_g3.make_server = original

    def __exit__(self, *args: object) -> None:
        # Terminate only our recorded Chrome process tree; HTTP cleanup always follows.
        for process in self.children:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=30)
        if self.chrome and self.chrome.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(self.chrome.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                pass
            # Windows taskkill can return while the tracked root remains alive.
            # Terminate our exact Popen handle, never a process selected by name.
            if self.chrome.poll() is None:
                self.chrome.kill()
            self.chrome.wait(timeout=30)
        self.client.close()
        if self.uvicorn:
            self.uvicorn.should_exit = True
            self.cp_thread.join(timeout=10)
            self.app.state.store.close()
        if self.fixture:
            self.fixture.shutdown()
            self.fixture.server_close()
            self.fixture_thread.join(timeout=5)

    def worker(self, mode: str = "tick", crash: str | None = None) -> None:
        route_fired = self.directory / "route-fired"
        route_restarted = self.directory / "route-restarted"
        previous = getattr(self, "persistent_worker", None)
        if route_fired.exists() and not route_restarted.exists():
            if previous is not None and previous.poll() is None:
                previous.kill()
                previous.wait(timeout=10)
            route_restarted.write_text("worker restarted after request loss")
        env = dict(
            os.environ,
            G3_CP_LOCAL=self.base,
            G3_CP_PUBLIC=self.public,
            G3_WORKER_TOKEN=self.config.worker_token,
            G3_SIGNING_KEY=self.config.signing_key.decode(),
            G3_CDP=self.cdp,
            CHAOS_NODE=self.node,
            CHAOS_PHASE=self.phase,
            CHAOS_VARIANT=self.variant,
            CHAOS_HTTP_RETRIES="3" if (self.directory / "route-armed").exists() else "",
        )
        fired = self.directory / "fault-fired"
        previously_fired = fired.exists()
        process = getattr(self, "persistent_worker", None)
        if process is None or process.poll() is not None:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("child.py")),
                    str(self.directory),
                ],
                env=env,
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.children.append(process)
            self.persistent_worker = process
        self.sequence = getattr(self, "sequence", 0) + 1
        sequence = self.sequence
        done = self.directory / "worker-done"
        process.stdin.write(
            (
                json.dumps({"mode": mode, "sequence": sequence, "crash": crash}) + "\n"
            ).encode()
        )
        process.stdin.flush()
        wait_for(
            lambda: (
                process.poll() is not None
                or (done.exists() and done.read_text() == str(sequence))
                or (not previously_fired and fired.exists())
                or (crash and (self.directory / "crash-ready").exists())
            ),
            "node boundary",
            float(os.environ.get("CHAOS_START_TIMEOUT", "60")),
        )
        if crash and (self.directory / "crash-ready").exists():
            process.kill()
            process.wait(10)
            return
        if not previously_fired and fired.exists() and process.poll() is None:
            process.kill()
            process.wait(10)
            # Restart immediately with the same checkpoint, Chrome and command databases.
            return self.worker(mode)
        if route_fired.exists() and not route_restarted.exists():
            return self.worker(mode)
        if process.poll() is not None:
            # Suppress subprocess exception strings: they may contain capability URLs.
            raise RuntimeError("chaos child failed; exit=" + str(process.returncode))
