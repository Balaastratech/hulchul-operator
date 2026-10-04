"""Secret validation regressions; all credentials here are synthetic."""
import pytest

from control_plane import config

GOOD = {
    "CP_SIGNING_KEY": "s" * 64,
    "CP_WORKER_TOKEN": "w" * 64,
    "CP_BASE_URL": "https://example.test",
}


@pytest.mark.parametrize("name", ["CP_SIGNING_KEY", "CP_WORKER_TOKEN", "CP_SIGNING_KEY_PREVIOUS"])
def test_rejects_same_key_template_placeholder(name, tmp_path, monkeypatch):
    placeholder = "synthetic-committed-placeholder-" * 3
    template = tmp_path / "example"
    template.write_text(f"{name}={placeholder}\n", encoding="utf-8")
    monkeypatch.setattr(config, "EXAMPLE_ENV_FILE", template)
    with pytest.raises(config.ConfigError, match=r"matches committed .env.example") as error:
        config.load_config(environ={**GOOD, name: placeholder})
    assert error.value.variable == name
    assert placeholder not in str(error.value)


@pytest.mark.parametrize("name", ["CP_SIGNING_KEY", "CP_WORKER_TOKEN", "CP_SIGNING_KEY_PREVIOUS"])
def test_secret_length_is_utf8_bytes(name):
    with pytest.raises(config.ConfigError, match="minimum 32 bytes"):
        config.load_config(environ={**GOOD, name: "a" * 31})
    config.load_config(environ={**GOOD, name: "é" * 16})


def test_template_empty_slots_and_different_key_are_allowed(tmp_path, monkeypatch):
    template = tmp_path / "example"
    template.write_text("CP_SIGNING_KEY=\nCP_WORKER_TOKEN=\nOTHER=" + GOOD["CP_SIGNING_KEY"])
    monkeypatch.setattr(config, "EXAMPLE_ENV_FILE", template)
    assert config.load_config(environ=GOOD).signing_key == GOOD["CP_SIGNING_KEY"].encode()


def test_template_does_not_interpolate_process_credentials(tmp_path, monkeypatch):
    template = tmp_path / "example"
    template.write_text("CP_SIGNING_KEY=${REAL_SECRET}\n")
    monkeypatch.setenv("REAL_SECRET", GOOD["CP_SIGNING_KEY"])
    monkeypatch.setattr(config, "EXAMPLE_ENV_FILE", template)
    config.load_config(environ=GOOD)
