"""Token service and config tests (CONTROL_PLANE_API.md sections 5, 5.7, 9.4).

Keys are generated inside the tests; the real .env is never read and there is no network.
"""
import base64
import hashlib
import hmac
import json
import secrets

import pytest

from control_plane import tokens as tokens_module
from control_plane.config import ConfigError, load_config, load_config_or_exit
from control_plane.tokens import (
    MAX_LIFETIME_S,
    CpError,
    TokenService,
    b64u,
    derive_kid,
    token_hash,
    utc_iso,
)

HASH = "ab" * 32
T0 = 1_800_000_000


class Clock:
    def __init__(self, t: int = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def key() -> bytes:
    return secrets.token_bytes(48)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def svc(key, clock) -> TokenService:
    return TokenService(key, clock=clock)


def act(svc, action="approve", **kw):
    kw.setdefault("job", "J1")
    kw.setdefault("snapshot_hash", HASH)
    if action in ("edit", "answer"):
        kw.setdefault("field_key", "email")
    return svc.mint("act", "R1", action=action, **kw)


def fail(fn, *args, **kwargs) -> CpError:
    with pytest.raises(CpError) as info:
        fn(*args, **kwargs)
    return info.value


def payload_of(token: str) -> dict:
    part = token.split(".")[2]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


# ------------------------------------------------------------------- format
def test_token_format_and_mac_match_the_spec(svc, key):
    token = act(svc)
    version, kid, payload_b64, mac_b64 = token.split(".")
    assert version == "v1" and kid == derive_kid(key) == svc.kid and len(kid) == 8
    message = b"hulchul.cp.token.v1\n" + kid.encode() + b"." + payload_b64.encode()
    assert mac_b64 == b64u(hmac.new(key, message, hashlib.sha256).digest())
    assert "=" not in token  # unpadded base64url


def test_payload_is_canonical_json_with_integer_times(svc, clock):
    token = act(svc, "edit")
    raw = base64.urlsafe_b64decode(token.split(".")[2] + "==").decode()
    data = json.loads(raw)
    assert raw == json.dumps(data, sort_keys=True, separators=(",", ":"))
    assert type(data["iat"]) is int and type(data["exp"]) is int
    assert data["iat"] == clock.t and data["exp"] == clock.t + 1800
    assert data["v"] == 1 and data["kid"] == svc.kid and data["field_key"] == "email"
    assert len(base64.urlsafe_b64decode(data["nonce"] + "==")) == 16  # 128-bit nonce


def test_kid_is_a_derived_label_not_a_key_digest(key):
    assert derive_kid(key) != hashlib.sha256(key).hexdigest()[:8]
    assert derive_kid(key) == derive_kid(key)
    assert derive_kid(key) != derive_kid(secrets.token_bytes(48))


def test_nonce_is_unique_per_mint(svc):
    assert len({act(svc) for _ in range(200)}) == 200


def test_verify_roundtrip_returns_claims(svc, clock):
    claims = svc.verify(act(svc, "edit"), "act")
    assert (claims.typ, claims.run, claims.job, claims.action) == ("act", "R1", "J1", "edit")
    assert claims.snapshot_hash == HASH and claims.field_key == "email"
    assert claims.exp - claims.iat == 1800 and len(claims.tid) == 8


def test_token_hash_is_sha256_of_the_token_string(svc):
    token = act(svc)
    assert token_hash(token) == hashlib.sha256(token.encode()).hexdigest()


def test_utc_iso_uses_explicit_offset_not_z():
    text = utc_iso(T0)
    assert text.endswith("+00:00") and "Z" not in text


# ---------------------------------------------------------------- lifetimes
def test_mint_clamps_lifetime_per_type(svc):
    cases = {
        "view": dict(),
        "run": dict(),
        "act": dict(job="J1", action="approve", snapshot_hash=HASH),
        "evd": dict(job="J1", field_key="ev_1"),
    }
    for typ, extra in cases.items():
        token = svc.mint(typ, "R1", ttl=10**9, **extra)
        data = payload_of(token)
        assert data["exp"] - data["iat"] == MAX_LIFETIME_S[typ]
    assert MAX_LIFETIME_S == {"view": 86400, "act": 1800, "run": 86400, "evd": 900}


def test_default_and_shorter_ttl(svc):
    data = payload_of(svc.mint("view", "R1"))
    assert data["exp"] - data["iat"] == 86400
    assert payload_of(svc.mint("view", "R1", ttl=60))["exp"] - T0 == 60


def test_expired_at_exp_is_410_and_one_second_before_is_valid(svc, clock):
    token = act(svc)
    clock.t = T0 + 1799
    assert svc.verify(token, "act").run == "R1"
    clock.t = T0 + 1800
    err = fail(svc.verify, token, "act")
    assert (err.code, err.status) == ("token_expired", 410)


def test_view_token_expires_after_24h(svc, clock):
    token = svc.mint("view", "R1")
    clock.t = T0 + 86399
    svc.verify(token, "view")
    clock.t = T0 + 86400
    assert fail(svc.verify, token, "view").status == 410


def test_bad_signature_beats_expiry(svc, clock):
    token = act(svc)
    clock.t = T0 + 10_000
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    assert fail(svc.verify, tampered, "act").status == 401  # not 410


def test_future_iat_beyond_skew_is_rejected(key, clock):
    minting = TokenService(key, clock=Clock(T0 + 3600))
    token = minting.mint("view", "R1")
    checking = TokenService(key, clock=clock)
    assert fail(checking.verify, token, "view").status == 401
    minting_slightly_ahead = TokenService(key, clock=Clock(T0 + 30))
    checking.verify(minting_slightly_ahead.mint("view", "R1"), "view")  # within 60 s skew


# ------------------------------------------------------------------ tampering
def test_tampered_payload_byte_is_401(svc):
    version, kid, payload_b64, mac = act(svc).split(".")
    flipped = ("B" if payload_b64[5] != "B" else "C")
    bad = ".".join([version, kid, payload_b64[:5] + flipped + payload_b64[6:], mac])
    err = fail(svc.verify, bad, "act")
    assert (err.code, err.status) == ("invalid_token", 401)


def test_tampered_mac_is_401(svc):
    version, kid, payload_b64, mac = act(svc).split(".")
    for bad_mac in (mac[:-1] + ("A" if mac[-1] != "A" else "B"), mac[:-4], mac + "A", ""):
        assert fail(svc.verify, ".".join([version, kid, payload_b64, bad_mac]), "act").status == 401


def test_payload_swapped_from_another_token_is_401(svc):
    a, b = act(svc).split("."), act(svc).split(".")
    a[2] = b[2]
    assert fail(svc.verify, ".".join(a), "act").status == 401


def test_unknown_kid_wrong_version_and_malformed_are_401(svc):
    version, kid, payload_b64, mac = act(svc).split(".")
    other = "0" * 8 if kid != "0" * 8 else "1" * 8
    candidates = [
        ".".join([version, other, payload_b64, mac]),
        ".".join(["v2", kid, payload_b64, mac]),
        ".".join([version, kid, payload_b64]),
        ".".join([version, kid, payload_b64, mac, "x"]),
        ".".join([version, "ZZZZZZZZ", payload_b64, mac]),
        ".".join([version, kid, "not base64!", mac]),
        "", "v1....", "garbage",
    ]
    for bad in candidates:
        assert fail(svc.verify, bad, "act").status == 401, bad
    assert fail(svc.verify, None, "act").status == 401


def test_overlong_token_rejected_before_crypto(svc, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("crypto must not run")

    monkeypatch.setattr(tokens_module.hmac, "new", boom)
    assert fail(svc.verify, "v1." + "a" * 1100, "act").status == 401


def test_token_signed_by_another_key_is_401(svc):
    other = TokenService(secrets.token_bytes(48), clock=Clock())
    assert fail(svc.verify, other.mint("view", "R1"), "view").status == 401


def test_mac_is_compared_in_constant_time(svc, monkeypatch):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(
        tokens_module.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b)
    )
    svc.verify(act(svc), "act")
    assert calls


def test_payload_with_extra_or_wrongly_typed_fields_is_401(key, clock):
    svc = TokenService(key, clock=clock)
    good = payload_of(act(svc))
    cases = [
        {**good, "extra": 1},
        {**good, "exp": float(good["exp"])},
        {**good, "exp": True},
        {**good, "v": 2},
        {**good, "kid": "deadbeef"},
        {**good, "typ": "root"},
        {**good, "run": "../x"},
        {**good, "exp": good["iat"] + 99999},
        {k: v for k, v in good.items() if k != "nonce"},
    ]
    for data in cases:
        payload_b64 = b64u(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())
        mac = svc._mac(key, svc.kid, payload_b64)  # correctly signed but structurally wrong
        assert fail(svc.verify, f"v1.{svc.kid}.{payload_b64}.{mac}", "act").status == 401, data


# ------------------------------------------------------------- type and scope
def test_wrong_typ_is_401_both_ways(svc):
    assert fail(svc.verify, svc.mint("view", "R1"), "act").status == 401  # view used on POST
    assert fail(svc.verify, act(svc), "view").status == 401  # act used on GET
    assert fail(svc.verify, svc.mint("run", "R1"), "act").status == 401
    svc.verify(svc.mint("view", "R1"), ("view", "run"))


def test_bindings_wrong_scope_is_403(svc):
    claims = svc.verify(act(svc, "edit"), "act")
    check = svc.check_bindings
    check(claims, action="edit", field_key="email")
    check(claims, action="edit", field_key="email", run_id="R1", job_id="J1")
    for kwargs in (
        dict(action="approve", field_key="email"),  # wrong action for this token
        dict(action="edit", field_key="phone"),  # wrong field
        dict(action="edit", field_key=None),
        dict(action="edit", field_key="email", run_id="R2"),  # another run
        dict(action="edit", field_key="email", job_id="J2"),  # another job
    ):
        err = fail(check, claims, **kwargs)
        assert (err.code, err.status) == ("forbidden", 403), kwargs


def test_run_token_binds_control_action_only(svc):
    claims = svc.verify(svc.mint("run", "R1"), "run")
    svc.check_bindings(claims, action="control")
    assert fail(svc.check_bindings, claims, action="approve").status == 403
    assert claims.job is None and claims.snapshot_hash is None


def test_mint_rejects_malformed_requests(svc):
    bad_calls = [
        lambda: svc.mint("admin", "R1"),
        lambda: svc.mint("view", "../R1"),
        lambda: svc.mint("view", "R1", action="approve"),
        lambda: svc.mint("act", "R1", action="approve", snapshot_hash=HASH),  # no job
        lambda: svc.mint("act", "R1", job="J1", action="approve"),  # no hash
        lambda: svc.mint("act", "R1", job="J1", action="approve", snapshot_hash="xyz"),
        lambda: svc.mint("act", "R1", job="J1", action="pause", snapshot_hash=HASH),
        lambda: svc.mint("act", "R1", job="J1", action="edit", snapshot_hash=HASH),  # no field
        lambda: svc.mint("act", "R1", job="J1", action="approve", snapshot_hash=HASH,
                         field_key="x"),
        lambda: svc.mint("run", "R1", job="J1"),
        lambda: svc.mint("evd", "R1", job="J1"),
    ]
    for call in bad_calls:
        with pytest.raises(ValueError):
            call()


# -------------------------------------------------------------- key handling
def test_short_or_wrong_type_signing_key_fails_closed():
    for bad in (b"", b"x" * 31, "a" * 64):
        with pytest.raises(ValueError) as info:
            TokenService(bad)  # type: ignore[arg-type]
        assert "CP_SIGNING_KEY" in str(info.value)
    TokenService(b"x" * 32)
    with pytest.raises(ValueError):
        TokenService(b"x" * 32, previous_key=b"short")


def test_repr_never_shows_key_material(svc, key):
    assert key.hex() not in repr(svc) and str(key) not in repr(svc)


def test_rotation_previous_key_verifies_old_tokens_only(key, clock):
    old = TokenService(key, clock=clock)
    old_view = old.mint("view", "R1")
    old_act = old.mint("act", "R1", job="J1", action="approve", snapshot_hash=HASH)
    new_key = secrets.token_bytes(48)
    rotated = TokenService(new_key, previous_key=key, clock=clock)
    assert rotated.kid != old.kid
    assert rotated.verify(old_view, "view").run == "R1"  # previous key still verifies
    assert rotated.verify(old_act, "act").kid == old.kid
    assert payload_of(rotated.mint("view", "R1"))["kid"] == rotated.kid  # new tokens: new kid
    revoked = TokenService(new_key, clock=clock)  # emergency revocation: previous removed
    assert fail(revoked.verify, old_view, "view").status == 401


def test_minting_module_has_no_database_dependency():
    assert not hasattr(tokens_module, "sqlite3")
    assert "store" not in vars(tokens_module)


# -------------------------------------------------------------------- config
GOOD_ENV = {
    "CP_SIGNING_KEY": "k" * 40,
    "CP_WORKER_TOKEN": "w" * 40,
    "CP_BASE_URL": "https://cp.example.test",
}


def config_error(env: dict) -> ConfigError:
    with pytest.raises(ConfigError) as info:
        load_config(environ=env)
    return info.value


def test_config_loads_good_environment():
    cfg = load_config(environ={**GOOD_ENV, "TELEGRAM_BOT_TOKEN": "t" * 20, "TELEGRAM_CHAT_ID": "1, 2"})
    assert cfg.signing_key == b"k" * 40 and cfg.env == "prod"
    assert cfg.telegram_chat_ids == frozenset({"1", "2"}) and cfg.telegram_enabled
    assert cfg.base_origin == "https://cp.example.test"
    assert cfg.db_path.name == "control_plane.sqlite" and ".state" in cfg.db_path.parts


def test_config_missing_or_short_signing_key_fails_closed_without_leaking():
    for value in (None, "", "   ", "short-secret-value-xyz"):
        env = {**GOOD_ENV}
        env.pop("CP_SIGNING_KEY")
        if value is not None:
            env["CP_SIGNING_KEY"] = value
        err = config_error(env)
        assert err.variable == "CP_SIGNING_KEY"
        assert "short-secret-value-xyz" not in str(err)


def test_config_exit_code_2_and_message_names_variable_only(capsys):
    env = {**GOOD_ENV, "CP_SIGNING_KEY": "super-secret-but-too-short"}
    with pytest.raises(SystemExit) as info:
        load_config_or_exit(environ=env)
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert "CP_SIGNING_KEY" in err and "super-secret" not in err


def test_config_prod_requirements():
    for drop in ("CP_WORKER_TOKEN", "CP_BASE_URL"):
        env = {k: v for k, v in GOOD_ENV.items() if k != drop}
        assert config_error(env).variable == drop
    assert config_error({**GOOD_ENV, "CP_BASE_URL": "http://cp.example.test"}).variable == "CP_BASE_URL"
    assert config_error({**GOOD_ENV, "CP_WORKER_TOKEN": "short"}).variable == "CP_WORKER_TOKEN"
    same = {**GOOD_ENV, "CP_WORKER_TOKEN": GOOD_ENV["CP_SIGNING_KEY"]}
    assert config_error(same).variable == "CP_WORKER_TOKEN"
    unauth = {**GOOD_ENV, "CP_DEV_ALLOW_UNAUTH_WORKER": "1"}
    assert config_error(unauth).variable == "CP_DEV_ALLOW_UNAUTH_WORKER"
    assert config_error({**GOOD_ENV, "CP_ENV": "staging"}).variable == "CP_ENV"


def test_config_dev_mode_rules():
    dev = {"CP_SIGNING_KEY": "k" * 40, "CP_ENV": "dev", "CP_DEV_ALLOW_UNAUTH_WORKER": "1"}
    cfg = load_config(environ=dev)
    assert cfg.worker_token is None and cfg.base_url.startswith("http://127.0.0.1")
    remote = {**dev, "CP_BASE_URL": "https://cp.example.test"}
    assert config_error(remote).variable == "CP_DEV_ALLOW_UNAUTH_WORKER"
    no_bearer = {"CP_SIGNING_KEY": "k" * 40, "CP_ENV": "dev"}
    assert config_error(no_bearer).variable == "CP_WORKER_TOKEN"


def test_config_env_file_is_overridden_by_environment(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "CP_SIGNING_KEY=" + "f" * 40 + "\nCP_WORKER_TOKEN=" + "g" * 40
        + "\nCP_BASE_URL=https://from-file.example.test\n",
        encoding="utf-8",
    )
    cfg = load_config(environ={"CP_BASE_URL": "https://from-env.example.test"}, env_file=env_file)
    assert cfg.base_url == "https://from-env.example.test"  # environment wins
    assert cfg.signing_key == b"f" * 40  # file supplies the rest


def test_config_repr_redacts_secrets():
    cfg = load_config(environ={**GOOD_ENV, "TELEGRAM_BOT_TOKEN": "tg-bot-token-value"})
    text = repr(cfg)
    for secret in ("k" * 40, "w" * 40, "tg-bot-token-value"):
        assert secret not in text


def test_previous_key_must_be_long_enough():
    assert config_error({**GOOD_ENV, "CP_SIGNING_KEY_PREVIOUS": "short"}).variable == (
        "CP_SIGNING_KEY_PREVIOUS"
    )
    cfg = load_config(environ={**GOOD_ENV, "CP_SIGNING_KEY_PREVIOUS": "p" * 40})
    assert cfg.signing_key_previous == b"p" * 40
