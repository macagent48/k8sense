"""PermissionMode + resolve() priority chain: flag > env > config > default."""

import pytest

from k8sense.permissions import (
    DEFAULT_MODE,
    ENV_VAR,
    PermissionMode,
    resolve,
)


def test_permission_mode_values():
    assert PermissionMode.READONLY.value == "readonly"
    assert PermissionMode.PROPOSE.value == "propose"
    assert PermissionMode.AUTO_SAFE.value == "auto-safe"


def test_default_mode_is_readonly():
    assert DEFAULT_MODE == PermissionMode.READONLY


def test_resolve_returns_default_when_no_overrides(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", tmp_path / "absent.toml")
    assert resolve() == PermissionMode.READONLY


def test_resolve_uses_flag_when_provided(monkeypatch):
    # Flag wins even when env is set
    monkeypatch.setenv(ENV_VAR, "auto-safe")
    assert resolve(flag_value="propose") == PermissionMode.PROPOSE


def test_resolve_raises_on_invalid_flag():
    with pytest.raises(ValueError, match="invalid permission mode"):
        resolve(flag_value="dangerous")


def test_resolve_uses_env_when_no_flag(monkeypatch, tmp_path):
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", tmp_path / "absent.toml")
    monkeypatch.setenv(ENV_VAR, "auto-safe")
    assert resolve() == PermissionMode.AUTO_SAFE


def test_resolve_falls_through_invalid_env_to_default(monkeypatch, tmp_path):
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", tmp_path / "absent.toml")
    monkeypatch.setenv(ENV_VAR, "nonsense")
    assert resolve() == PermissionMode.READONLY


def test_resolve_uses_config_when_no_flag_or_env(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    cfg = tmp_path / "config.toml"
    cfg.write_text('permission_mode = "propose"\n', encoding="utf-8")
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", cfg)
    assert resolve() == PermissionMode.PROPOSE


def test_resolve_falls_through_invalid_config_to_default(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    cfg = tmp_path / "config.toml"
    cfg.write_text('permission_mode = "nonsense"\n', encoding="utf-8")
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", cfg)
    assert resolve() == PermissionMode.READONLY


def test_resolve_falls_through_malformed_toml_to_default(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    cfg = tmp_path / "config.toml"
    cfg.write_text("permission_mode = unquoted-value", encoding="utf-8")
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", cfg)
    assert resolve() == PermissionMode.READONLY
