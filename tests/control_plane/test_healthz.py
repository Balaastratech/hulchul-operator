"""Health probes are unauthenticated, minimal and read-only."""
import sqlite3

from fastapi.testclient import TestClient

from control_plane.app import create_app
from control_plane.config import load_config


def test_health_probe_never_changes_database(tmp_path):
    cfg = load_config(environ={"CP_SIGNING_KEY": "s" * 64, "CP_WORKER_TOKEN": "w" * 64,
                               "CP_BASE_URL": "https://example.test", "CP_DB_PATH": str(tmp_path / "cp.sqlite")})
    with TestClient(create_app(cfg)) as client:
        with sqlite3.connect(cfg.db_path) as db:
            before = list(db.iterdump())
        response = client.get("/healthz")
        assert response.status_code == 200 and response.json() == {"status": "ok"}
        assert response.headers["cache-control"] == "no-store"
        with sqlite3.connect(cfg.db_path) as db:
            assert list(db.iterdump()) == before
