"""Read-only prerequisite checks; never print credential values or response bodies."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]


def chrome_path() -> str | None:
    """Find system Chrome using the launcher's precedence and platform locations."""
    override = os.environ.get("CHROME_PATH")
    if override:
        return override if Path(override).is_file() else None
    candidates = [shutil.which("chrome"), shutil.which("google-chrome-stable"),
                  shutil.which("google-chrome"),
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  str(Path(os.environ.get("LOCALAPPDATA", "~")) / "Google/Chrome/Application/chrome.exe"),
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return next((str(p) for p in candidates if p and Path(p).is_file()), None)


def port_free(host: str, port: int) -> bool:
    """Check exclusive bind availability without starting or stopping a service."""
    with socket.socket() as sock:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def probe_provider(config: dict[str, str], provider: str) -> tuple[str, str]:
    """Authenticated metadata GET only; no generation, paid jobs or secret output."""
    import requests

    model = config.get("LLM_MODEL") or "gemini-2.5-flash"
    if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
        return "FAIL", "LLM_MODEL: use a bare model name such as gemini-2.5-flash."
    try:
        if provider == "gemini_api":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            headers = {"x-goog-api-key": config["GEMINI_API_KEY"]}
        else:
            import google.auth
            from google.auth.transport.requests import Request

            project = config["GOOGLE_CLOUD_PROJECT"]
            location = config.get("GOOGLE_CLOUD_LOCATION") or "global"
            if not all(re.fullmatch(r"[A-Za-z0-9._:-]+", v) for v in (project, location)):
                return "FAIL", "Set a valid GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION."
            # Bound ADC token refresh as well as the metadata request.
            class BoundedRequest(Request):
                def __call__(self, *args, **kwargs):
                    kwargs["timeout"] = 10
                    return super().__call__(*args, **kwargs)

            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            credentials.refresh(BoundedRequest())
            hostname = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
            # PublisherModel.get uses the catalog resource, not the inference path.
            # https://cloud.google.com/vertex-ai/docs/reference/rest/v1/publishers.models/get
            url = f"https://{hostname}/v1/publishers/google/models/{model}"
            headers = {"Authorization": "Bearer " + credentials.token,
                       "x-goog-user-project": project}
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=False)
        if response.status_code == 200:
            return "PASS", "Provider model metadata reachable; generation/quota not tested."
        return "FAIL", f"Provider metadata HTTP {response.status_code}; check key/ADC, model, project IAM, Vertex enablement and billing."
    except Exception:
        return "FAIL", "Provider check failed; check internet/HTTPS and key or run gcloud auth application-default login. Details suppressed."


def main() -> int:
    """Print PASS/WARN/FAIL and return nonzero for required prerequisite failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="skip provider HTTP checks")
    parser.add_argument("--real", action="store_true", help="require selected model credentials and online reachability")
    parser.add_argument("--cp-port", type=int, default=8790)
    parser.add_argument("--fixture-host", choices=["127.0.0.1", "127.0.0.2"], default="127.0.0.1")
    args = parser.parse_args()
    failures = 0

    def report(status: str, message: str) -> None:
        nonlocal failures
        failures += status == "FAIL"
        print(f"{status} {message}")

    report("PASS" if sys.version_info >= (3, 13) else "FAIL",
           "Python >=3.13." if sys.version_info >= (3, 13) else "Python: install 3.13+ and recreate .venv with scripts/setup.")
    found = chrome_path()
    report("PASS" if found else "FAIL", "System Chrome found (no bundled browser needed)." if found else
           "Chrome: install Google Chrome stable or set CHROME_PATH to its full executable path.")
    if found and not os.environ.get("CHROME_PATH") and found != shutil.which("chrome") and found != r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe":
        report("WARN", "Set CHROME_PATH to the installed Chrome executable: demo/real launchers otherwise use their Windows x86 fallback.")
    for host, port in [(args.fixture_host, 8780), ("127.0.0.1", args.cp_port)]:
        free = 1 <= port <= 65535 and port_free(host, port)
        report("PASS" if free else "FAIL", f"Port {host}:{port} free." if free else
               f"Port {host}:{port}: stop only your own listener; identify with Get-NetTCPConnection or lsof. CP can use --cp-port; real fixtures can use --fixture-host.")
    free_gb = shutil.disk_usage(ROOT).free / 1024**3
    report("PASS" if free_gb >= 2 else "WARN", "Disk >=2 GB free." if free_gb >= 2 else "Disk: free at least 2 GB for environment and run artifacts; preserve evidence.")
    report("WARN", "RAM: allow 8 GB total / ~2 GB available for Chrome; recommended, not a measured minimum.")
    try:
        from dotenv import dotenv_values
        env_file = Path(os.environ.get("ENV_FILE") or ROOT / ".env")
        # No interpolation: configuration text cannot pull private unrelated variables.
        config = {k: v for k, v in dotenv_values(env_file, interpolate=False).items() if v}
        config.update(os.environ)
        report("PASS" if env_file.is_file() else "WARN", ".env selected file present (values hidden)." if env_file.is_file() else
               ".env missing: run setup or copy .env.example for real runs; scripted demo needs no file.")
    except Exception:
        report("FAIL", "Environment check: run pip install .; ensure ENV_FILE is a readable local dotenv file. Details suppressed.")
        return 1
    provider = config.get("LLM_PROVIDER", "vertex").strip()
    if provider not in {"vertex", "gemini_api"}:
        report("FAIL" if args.real else "WARN", "Set LLM_PROVIDER=vertex or gemini_api for a real run.")
    key_name = "GEMINI_API_KEY" if provider == "gemini_api" else "GOOGLE_CLOUD_PROJECT"
    present = bool(config.get(key_name, "").strip())
    report("PASS" if present else ("FAIL" if args.real else "WARN"), f"{key_name} present (hidden)." if present else
           f"{key_name} absent: set it in .env and select LLM_PROVIDER; no keys needed for scripted demo.")
    for name in ("CP_SIGNING_KEY", "CP_WORKER_TOKEN"):
        value = config.get(name, "")
        valid = len(value.encode()) >= 32 and value.lower() not in {"change-me", "replace-me"}
        report("PASS" if valid else "WARN", f"{name} present, >=32 bytes (hidden)." if valid else
               f"{name}: generate secrets.token_urlsafe(48) for standalone CP; demo/run_real generate per-run secrets automatically.")
    if args.offline:
        report("WARN", "Provider reachability skipped (--offline); run doctor.py --real with configured credentials to check metadata.")
    elif present and provider in {"vertex", "gemini_api"}:
        status, message = probe_provider(config, provider)
        report(status if args.real or status == "PASS" else "WARN", message)
    else:
        report("WARN", "Gemini/Vertex not probed: configure the selected provider and run doctor.py --real.")
    telegram = all(config.get(n, "").strip() for n in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"))
    report("PASS" if telegram else "WARN", "Optional Telegram token + chat id present (hidden)." if telegram else
           "Optional Telegram: set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID for phone notifications, or use --no-telegram.")
    for tool in ("cloudflared", "docker"):
        present_tool = bool(shutil.which(tool))
        report("PASS" if present_tool else "WARN", f"Optional {tool} executable found (service not probed)." if present_tool else
               f"Optional {tool}: install it for {'--tunnel phone approval' if tool == 'cloudflared' else 'container deployment'}; local demo does not need it.")
    print(f"Doctor: {failures} required failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
