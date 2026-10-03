"""Control plane configuration (CONTROL_PLANE_API.md 1.1, 5.7, 9.4).

This is the ONLY module that reads the .env file (python-dotenv), and only when
`load_config` is called, never at import time. Secret values are never printed,
logged or put in error messages: `ConfigError` names the variable and the problem
class only, and `Config.__repr__` redacts.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_ENV_FILE = r"C:\Balaastra\hulchul-operator\.env"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / ".state" / "control_plane.sqlite"
MIN_SECRET_BYTES = 32
EXAMPLE_ENV_FILE = Path(__file__).resolve().parents[1] / ".env.example"
EXIT_CONFIG = 2
_LOOPBACK_PREFIXES = ("http://127.0.0.1", "http://localhost", "http://[::1]")


class ConfigError(Exception):
    """Raised with the variable name and a generic problem; never a value."""

    def __init__(self, variable: str, problem: str) -> None:
        super().__init__(f"{variable}: {problem}")
        self.variable = variable
        self.problem = problem


@dataclass(frozen=True)
class Config:
    signing_key: bytes = field(repr=False)
    signing_key_previous: bytes | None = field(repr=False)
    worker_token: str | None = field(repr=False)
    base_url: str
    db_path: Path
    env: str
    dev_allow_unauth_worker: bool
    telegram_bot_token: str | None = field(repr=False)
    telegram_chat_ids: frozenset[str]

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_ids)

    @property
    def base_origin(self) -> str:
        """scheme://host[:port] of CP_BASE_URL, used for the Origin check and links."""
        from urllib.parse import urlsplit

        parts = urlsplit(self.base_url)
        return f"{parts.scheme}://{parts.netloc}"

    def __repr__(self) -> str:
        return (
            f"Config(env={self.env!r}, base_url={self.base_url!r}, "
            f"db_path={str(self.db_path)!r}, telegram_enabled={self.telegram_enabled})"
        )


def _secret(values: Mapping[str, str | None], name: str, *, required: bool) -> str | None:
    raw = values.get(name)
    if raw is None or raw.strip() == "":
        if required:
            raise ConfigError(name, "missing")
        return None
    if len(raw.encode("utf-8")) < MIN_SECRET_BYTES:
        raise ConfigError(name, f"too short (minimum {MIN_SECRET_BYTES} bytes)")
    # Read only the committed template, never another user's environment. Disable
    # interpolation so a template reference cannot resolve to a real credential.
    example = dotenv_values(EXAMPLE_ENV_FILE, interpolate=False).get(name)
    if example and raw == example:
        raise ConfigError(name, "matches committed .env.example; generate a fresh random secret")
    return raw


def load_config(
    environ: Mapping[str, str] | None = None,
    env_file: str | os.PathLike[str] | None = None,
) -> Config:
    """Build a Config or raise ConfigError (fail closed).

    Without arguments: process environment over the .env named by ENV_FILE (default
    C:\\Balaastra\\hulchul-operator\\.env); real environment variables win over the file.
    With an explicit `environ` (tests) no .env is read unless `env_file` is given.
    """
    if environ is None:
        process_env: Mapping[str, str] = os.environ
        path = env_file or process_env.get("ENV_FILE") or DEFAULT_ENV_FILE
    else:
        process_env = environ
        path = env_file
    values: dict[str, str | None] = {}
    if path and Path(path).is_file():
        values.update(dotenv_values(path))
    values.update(process_env)  # environment wins over the file (override=False semantics)

    env = (values.get("CP_ENV") or "prod").strip().lower()
    if env not in {"prod", "dev"}:
        raise ConfigError("CP_ENV", "must be 'prod' or 'dev'")

    signing_key = _secret(values, "CP_SIGNING_KEY", required=True)
    previous = _secret(values, "CP_SIGNING_KEY_PREVIOUS", required=False)
    assert signing_key is not None
    if previous is not None and previous == signing_key:
        previous = None  # rotation overlap with the same key is meaningless

    dev_unauth = (values.get("CP_DEV_ALLOW_UNAUTH_WORKER") or "").strip() == "1"
    base_url = (values.get("CP_BASE_URL") or values.get("CP_PUBLIC_URL") or "").strip().rstrip("/")
    if env == "prod":
        if dev_unauth:
            raise ConfigError("CP_DEV_ALLOW_UNAUTH_WORKER", "not allowed unless CP_ENV=dev")
        if not base_url:
            raise ConfigError("CP_BASE_URL", "missing")
        if not base_url.startswith("https://"):
            raise ConfigError("CP_BASE_URL", "must be an https:// URL")
    else:
        if not base_url:
            base_url = "http://127.0.0.1:8790"
        if dev_unauth and not base_url.startswith(_LOOPBACK_PREFIXES):
            raise ConfigError("CP_DEV_ALLOW_UNAUTH_WORKER", "requires a loopback CP_BASE_URL")

    worker_token = _secret(values, "CP_WORKER_TOKEN", required=not dev_unauth)
    if worker_token is not None and worker_token == signing_key:
        raise ConfigError("CP_WORKER_TOKEN", "must differ from CP_SIGNING_KEY")

    db_raw = (values.get("CP_DB_PATH") or "").strip()
    bot_token = (values.get("TELEGRAM_BOT_TOKEN") or "").strip() or None
    chat_ids = frozenset(
        part.strip() for part in (values.get("TELEGRAM_CHAT_ID") or "").split(",") if part.strip()
    )
    return Config(
        signing_key=signing_key.encode("utf-8"),
        signing_key_previous=previous.encode("utf-8") if previous else None,
        worker_token=worker_token,
        base_url=base_url,
        db_path=Path(db_raw) if db_raw else DEFAULT_DB_PATH,
        env=env,
        dev_allow_unauth_worker=dev_unauth,
        telegram_bot_token=bot_token,
        telegram_chat_ids=chat_ids,
    )


def load_config_or_exit(
    environ: Mapping[str, str] | None = None,
    env_file: str | os.PathLike[str] | None = None,
) -> Config:
    """Process entry point helper: print the variable name only and exit 2."""
    try:
        return load_config(environ, env_file)
    except ConfigError as exc:
        print(f"control plane refuses to start: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG) from None
