"""HMAC capability tokens (CONTROL_PLANE_API.md section 5, D-008).

    token = "v1." kid "." b64u(payload_json) "." b64u(mac)
    mac   = HMAC-SHA256(key[kid], "hulchul.cp.token.v1\\n" || kid || "." || b64u(payload_json))

Minting is PURE: it reads the clock, os.urandom and the key, and returns a string.
It never touches a database, so rendering a page that embeds tokens (a GET) cannot
change state. Verification (steps 1-5 of section 5.4) is also database-free; the
database-dependent steps (replay, snapshot, state, atomic consume) live in store.py.

Nothing in this module logs. Callers log only `Claims.tid` (nonce prefix).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Iterable

TOKEN_VERSION = 1
MIN_KEY_BYTES = 32
MAX_TOKEN_LENGTH = 1024
MAX_IAT_SKEW_S = 60

_DOMAIN = b"hulchul.cp.token.v1\n"
_KID_LABEL = b"hulchul.cp.kid.v1"

# Maximum lifetime per token type (section 5.2).
MAX_LIFETIME_S: dict[str, int] = {
    "view": 24 * 3600,
    "act": 30 * 60,
    "run": 24 * 3600,
    "evd": 15 * 60,
}
TOKEN_TYPES = tuple(MAX_LIFETIME_S)

ACT_ACTIONS = ("approve", "edit", "reject", "skip", "answer", "handoff_done")
FIELD_ACTIONS = ("edit", "answer")  # act actions that carry a field_key
RUN_ACTION = "control"
EVD_ACTION = "evidence"

# Short links (/s/<code>): 64-bit HMAC code, valid 24 h after its 15-minute bucket.
SHORT_BUCKET_S = 15 * 60
SHORT_WINDOW_BUCKETS = 24 * 3600 // SHORT_BUCKET_S  # 96
SHORT_CODE_BYTES = 8
SHORT_MAX_TARGETS = 1000
SHORT_CODE_RE = re.compile(r"^[a-z2-7]{13}$")

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_B64U = re.compile(r"^[A-Za-z0-9_-]+$")
_KID = re.compile(r"^[0-9a-f]{8}$")


class CpError(Exception):
    """An error with the HTTP mapping of section 4.2 (`error` code + status)."""

    def __init__(self, code: str, status: int, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


class TokenError(CpError):
    """401 invalid_token / 410 token_expired / 403 forbidden."""


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    if not _B64U.match(text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def token_hash(token: str) -> str:
    """hex(SHA-256(token string)); the only form of a token ever stored or sent on."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utc_iso(ts: int | float) -> str:
    """UTC ISO-8601 with an explicit +00:00 offset (never 'Z'), as the worker needs."""
    return datetime.fromtimestamp(int(ts), UTC).isoformat()


def derive_kid(key: bytes) -> str:
    """Derived label (8 hex) saying which configured key signed a token.

    HMAC(key, fixed label): domain-separated from the token MAC and not a plain digest
    of the key.
    """
    return hmac.new(key, _KID_LABEL, hashlib.sha256).hexdigest()[:8]


def _canonical_payload(payload: dict) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True
    )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _opt_str(value: object) -> bool:
    return value is None or isinstance(value, str)


@dataclass(frozen=True)
class Claims:
    v: int
    typ: str
    run: str
    job: str | None
    action: str | None
    snapshot_hash: str | None
    field_key: str | None
    iat: int
    exp: int
    nonce: str
    kid: str

    @property
    def tid(self) -> str:
        """Token id prefix: the only token-derived value that may appear in logs."""
        return self.nonce[:8]


class TokenService:
    """Mints and verifies tokens for one signing key (plus an optional previous one)."""

    def __init__(
        self,
        signing_key: bytes,
        previous_key: bytes | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(signing_key, (bytes, bytearray)) or len(signing_key) < MIN_KEY_BYTES:
            raise ValueError(f"CP_SIGNING_KEY must be at least {MIN_KEY_BYTES} bytes")
        if previous_key is not None and len(previous_key) < MIN_KEY_BYTES:
            raise ValueError(f"CP_SIGNING_KEY_PREVIOUS must be at least {MIN_KEY_BYTES} bytes")
        self._key = bytes(signing_key)
        self._kid = derive_kid(self._key)
        self._keys: dict[str, bytes] = {self._kid: self._key}
        if previous_key is not None:
            previous = bytes(previous_key)
            self._keys.setdefault(derive_kid(previous), previous)  # verify-only
        self._clock = clock

    def __repr__(self) -> str:  # never show key material
        return f"TokenService(kid={self._kid})"

    @property
    def kid(self) -> str:
        return self._kid

    def now(self) -> int:
        return int(self._clock())

    # ------------------------------------------------------------------ minting
    def mint(
        self,
        typ: str,
        run: str,
        *,
        job: str | None = None,
        action: str | None = None,
        snapshot_hash: str | None = None,
        field_key: str | None = None,
        ttl: int | None = None,
    ) -> str:
        """Pure minting (section 5.3). The ttl is clamped to the maximum of the type."""
        if typ not in MAX_LIFETIME_S:
            raise ValueError("unknown token type")
        if not ID_PATTERN.match(run):
            raise ValueError("invalid run id")
        if job is not None and not ID_PATTERN.match(job):
            raise ValueError("invalid job id")

        if typ == "view":
            if action is not None or snapshot_hash is not None or field_key is not None:
                raise ValueError("view tokens carry only run and optional job")
        elif typ == "run":
            if job is not None or snapshot_hash is not None or field_key is not None:
                raise ValueError("run tokens carry only the run")
            action = RUN_ACTION
        elif typ == "evd":
            if job is None or not field_key or snapshot_hash is not None:
                raise ValueError("evd tokens need job and the evidence id (field_key)")
            action = EVD_ACTION
        else:  # act
            if job is None:
                raise ValueError("act tokens need a job")
            if action not in ACT_ACTIONS:
                raise ValueError("unknown act action")
            if snapshot_hash is None or not _HEX64.match(snapshot_hash):
                raise ValueError("act tokens need a 64-hex snapshot or gate hash")
            if (action in FIELD_ACTIONS) != bool(field_key):
                raise ValueError("field_key is required for edit/answer and only for them")

        maximum = MAX_LIFETIME_S[typ]
        lifetime = maximum if ttl is None else max(1, min(int(ttl), maximum))
        iat = self.now()
        payload = {
            "v": TOKEN_VERSION,
            "typ": typ,
            "run": run,
            "job": job,
            "action": action,
            "snapshot_hash": snapshot_hash,
            "field_key": field_key,
            "iat": iat,
            "exp": iat + lifetime,
            "nonce": b64u(os.urandom(16)),
            "kid": self._kid,
        }
        payload_b64 = b64u(_canonical_payload(payload).encode("utf-8"))
        mac = self._mac(self._key, self._kid, payload_b64)
        return f"v{TOKEN_VERSION}.{self._kid}.{payload_b64}.{mac}"

    # ------------------------------------------------------- short links (/s/<code>)
    @staticmethod
    def _short_mac(key: bytes, run: str, job: str | None, bucket: int) -> str:
        message = f"hulchul.cp.short.v1\n{run}\n{job or ''}\n{bucket}".encode()
        digest = hmac.new(key, message, hashlib.sha256).digest()[:SHORT_CODE_BYTES]
        return base64.b32encode(digest).decode("ascii").rstrip("=").lower()

    def short_code(self, run: str, job: str | None = None) -> str:
        """Opaque 13-character code for a run page or a job page (COMMUNICATION_MATRIX 1).

        Stateless and pure: an HMAC over (run, job, 15-minute bucket). The code itself holds no
        token and no id. `match_short_code` recognises it for up to 24 h after the bucket it was
        made in, so a link in an old chat message stops working on its own."""
        if not ID_PATTERN.match(run) or (job is not None and not ID_PATTERN.match(job)):
            raise ValueError("invalid id")
        return self._short_mac(self._key, run, job, self.now() // SHORT_BUCKET_S)

    def match_short_code(
        self, code: str, targets: Iterable[tuple[str, str | None]]
    ) -> tuple[str, str | None, int] | None:
        """Find which known (run, job) a code was made for; returns (run, job, link_expiry).

        `targets` are the ids the control plane knows about. Read-only, constant-time compare,
        bounded work (`SHORT_MAX_TARGETS` ids x 97 buckets x the configured keys)."""
        if not isinstance(code, str) or not SHORT_CODE_RE.match(code):
            return None
        now_bucket = self.now() // SHORT_BUCKET_S
        found: tuple[str, str | None, int] | None = None
        for count, (run, job) in enumerate(targets):
            if count >= SHORT_MAX_TARGETS:
                break
            if not ID_PATTERN.match(run) or (job is not None and not ID_PATTERN.match(job)):
                continue
            for key in self._keys.values():
                for age in range(SHORT_WINDOW_BUCKETS + 1):
                    made = now_bucket - age
                    candidate = self._short_mac(key, run, job, made)
                    if hmac.compare_digest(candidate.encode("ascii"), code.encode("ascii")):
                        expiry = (made + SHORT_WINDOW_BUCKETS + 1) * SHORT_BUCKET_S
                        found = (run, job, expiry)  # keep scanning: no early-exit timing signal
        return found

    @staticmethod
    def _mac(key: bytes, kid: str, payload_b64: str) -> str:
        message = _DOMAIN + kid.encode("ascii") + b"." + payload_b64.encode("ascii")
        return b64u(hmac.new(key, message, hashlib.sha256).digest())

    # ------------------------------------------------------------- verification
    def verify(
        self,
        token: str,
        expected_typ: str | Iterable[str],
        *,
        now: int | None = None,
    ) -> Claims:
        """Steps 1-4 of section 5.4; no database access.

        401 invalid_token (format, kid, MAC, payload, type, iat) precedes
        410 token_expired. Bindings (403) are checked by `check_bindings`.
        """
        invalid = TokenError("invalid_token", 401)
        allowed = (expected_typ,) if isinstance(expected_typ, str) else tuple(expected_typ)

        # Step 1: length and format before any crypto, then select the key.
        if not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
            raise invalid
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != f"v{TOKEN_VERSION}":
            raise invalid
        _, kid, payload_b64, mac_b64 = parts
        if not _KID.match(kid) or not _B64U.match(payload_b64) or not _B64U.match(mac_b64):
            raise invalid
        key = self._keys.get(kid)
        if key is None:
            raise invalid

        # Step 2: MAC over the exact transmitted text; constant-time compare of the
        # canonical base64url strings (so non-canonical encodings are rejected too).
        expected_mac = self._mac(key, kid, payload_b64)
        if not hmac.compare_digest(expected_mac.encode("ascii"), mac_b64.encode("ascii")):
            raise invalid

        # Step 3: payload shape, kid, type, issue time.
        claims = self._parse_payload(payload_b64, kid)
        if claims is None or claims.typ not in allowed:
            raise invalid
        current = self.now() if now is None else int(now)
        if claims.iat > current + MAX_IAT_SKEW_S:
            raise invalid

        # Step 4: expiry.
        if current >= claims.exp:
            raise TokenError("token_expired", 410)
        return claims

    @staticmethod
    def _parse_payload(payload_b64: str, kid: str) -> Claims | None:
        try:
            raw = json.loads(
                b64u_decode(payload_b64).decode("utf-8"),
                parse_constant=_reject_constant,
            )
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None
        if not isinstance(raw, dict) or set(raw) != set(Claims.__dataclass_fields__):
            return None
        if raw["v"] != TOKEN_VERSION or not _is_int(raw["v"]) or raw["kid"] != kid:
            return None
        typ = raw["typ"]
        if typ not in MAX_LIFETIME_S:
            return None
        if not isinstance(raw["run"], str) or not ID_PATTERN.match(raw["run"]):
            return None
        if not (_opt_str(raw["job"]) and _opt_str(raw["action"]) and _opt_str(raw["field_key"])):
            return None
        snap = raw["snapshot_hash"]
        if snap is not None and not (isinstance(snap, str) and _HEX64.match(snap)):
            return None
        if not (_is_int(raw["iat"]) and _is_int(raw["exp"])):
            return None
        if raw["exp"] <= raw["iat"] or raw["exp"] - raw["iat"] > MAX_LIFETIME_S[typ]:
            return None
        if not isinstance(raw["nonce"], str) or len(raw["nonce"]) < 8:
            return None
        return Claims(**raw)

    @staticmethod
    def check_bindings(
        claims: Claims,
        *,
        action: str,
        field_key: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        """Step 5: 403 forbidden unless the token's bindings match the route and body.

        `field_key` is compared strictly (routes pass None for actions without one);
        `run_id`/`job_id` are only compared when the body supplied them.
        """
        if claims.action != action:
            raise TokenError("forbidden", 403, "action mismatch")
        if claims.field_key != field_key:
            raise TokenError("forbidden", 403, "field mismatch")
        if run_id is not None and run_id != claims.run:
            raise TokenError("forbidden", 403, "run mismatch")
        if job_id is not None and job_id != claims.job:
            raise TokenError("forbidden", 403, "job mismatch")


def _reject_constant(name: str):  # NaN / Infinity are not valid payloads
    raise ValueError(name)
