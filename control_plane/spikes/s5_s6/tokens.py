"""HMAC signed token helpers for the S5/S6 spike (synthetic, spike-grade).

Token format: base64url(payload_json) + "." + base64url(HMAC-SHA256(key, payload_b64)).
Payload (action): {typ, run, job, action, snapshot_hash, exp, nonce}.
Payload (view):   {typ, run, job, snapshot_hash, exp, ttl}. The view token only
lets a GET render the read-only page; it can never approve anything.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time


class TokenError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def snapshot_hash(run: str, job: str, version: str = "v1") -> str:
    """Fake snapshot hash: sha256 of a literal (spike only)."""
    return hashlib.sha256(f"spike-snapshot:{run}:{job}:{version}".encode()).hexdigest()


def _sign(key: bytes, payload_b64: str) -> str:
    return b64u(hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).digest())


def mint(key: bytes, payload: dict) -> str:
    payload_b64 = b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload_b64}.{_sign(key, payload_b64)}"


def mint_view(key: bytes, run: str, job: str, snap: str, ttl_action: int = 300, ttl_view: int = 3600) -> str:
    return mint(key, {"typ": "view", "run": run, "job": job, "snapshot_hash": snap,
                      "exp": int(time.time()) + ttl_view, "ttl": ttl_action})


def mint_action(key: bytes, run: str, job: str, snap: str, ttl: int = 300, exp: int | None = None) -> str:
    return mint(key, {"typ": "action", "run": run, "job": job, "action": "approve",
                      "snapshot_hash": snap,
                      "exp": int(time.time()) + ttl if exp is None else exp,
                      "nonce": secrets.token_hex(16)})  # 128-bit random


def verify(key: bytes, token: str) -> dict:
    """Return the payload if the signature is valid, else raise TokenError(403).

    Expiry and scope are checked by the caller so it can map them to 410/403/409.
    """
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        raise TokenError("malformed", 403)
    if not hmac.compare_digest(_sign(key, payload_b64), sig):
        raise TokenError("bad_signature", 403)
    try:
        payload = json.loads(b64u_decode(payload_b64))
    except Exception:
        raise TokenError("malformed", 403)
    if not isinstance(payload, dict):
        raise TokenError("malformed", 403)
    return payload
