"""`python -m control_plane`: run the control plane with uvicorn.

Binds 127.0.0.1 unless `--host` says otherwise. The configuration is loaded and validated
BEFORE anything is bound or created on disk: a missing or short CP_SIGNING_KEY (or any other
invalid setting) prints the variable name only, never a value, and exits with status 2.

The server runs with the access log off: request lines contain `?t=<view token>` and tokens
must never reach a log file.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .app import create_app
from .config import EXIT_CONFIG, ConfigError, load_config_or_exit

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790  # not 8765 (Bala Agent Mail), 8780 (fixture server) or 8781
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
GRACEFUL_SHUTDOWN_S = 5  # open event streams must not keep the process alive forever


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m control_plane",
        description="Hulchul control plane (worker API, review pages, event streams).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"bind address (default {DEFAULT_HOST}; a tunnel or proxy on this "
                        "machine connects to loopback)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"bind port (default {DEFAULT_PORT})")
    parser.add_argument("--env-file", default=None,
                        help="dotenv file (default: ENV_FILE from the environment)")
    parser.add_argument("--log-level", default="info",
                        choices=["critical", "error", "warning", "info", "debug"])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config_or_exit(env_file=args.env_file)  # fail closed before binding
    if config.worker_token is None:
        # The dev-only unauthenticated worker mode is for tests; a server started from the
        # command line always requires the worker bearer secret.
        print("control plane refuses to start: CP_WORKER_TOKEN: missing", file=sys.stderr)
        return EXIT_CONFIG
    try:
        app = create_app(config)
    except ConfigError as exc:
        print(f"control plane refuses to start: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    if args.host not in _LOOPBACK_HOSTS:
        print(
            f"warning: binding {args.host}, not loopback. There is no user login system; "
            "put TLS in front (HTTPS is required when deployed).",
            file=sys.stderr,
        )
    import uvicorn

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
