"""Permission mode resolution: CLI flag > env var > config file > default."""

from __future__ import annotations

import os
import tomllib
from enum import Enum
from pathlib import Path


class PermissionMode(str, Enum):
    READONLY = "readonly"
    PROPOSE = "propose"
    AUTO_SAFE = "auto-safe"


DEFAULT_MODE = PermissionMode.READONLY
CONFIG_PATH = Path.home() / ".k8sense" / "config.toml"
ENV_VAR = "K8SENSE_PERMISSION_MODE"


def _parse(value: str) -> PermissionMode | None:
    """Parse a string into a PermissionMode; None on unknown value."""
    try:
        return PermissionMode(value)
    except ValueError:
        return None


def _from_config_file() -> PermissionMode | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return None
    raw = data.get("permission_mode")
    return _parse(raw) if isinstance(raw, str) else None


def resolve(flag_value: str | None = None) -> PermissionMode:
    """Resolve effective mode: flag > env > config > default.

    Raises ValueError if `flag_value` is a non-None unknown string (CLI error).
    Bad env / config values silently fall through to lower-priority sources.
    """
    if flag_value is not None:
        parsed = _parse(flag_value)
        if parsed is None:
            raise ValueError(f"invalid permission mode flag value: {flag_value!r}")
        return parsed

    env = os.environ.get(ENV_VAR)
    if env:
        parsed = _parse(env)
        if parsed is not None:
            return parsed

    cfg = _from_config_file()
    if cfg is not None:
        return cfg

    return DEFAULT_MODE
