"""Prometheus tool: HTTP client + result formatting + SDK wrapper."""

from __future__ import annotations

import os
import re

DEFAULT_PROM_URL = "http://192.168.70.174:9090"
DEFAULT_TIMEOUT_S = 10.0
MAX_RESULT_LINES = 50
MAX_RESULT_CHARS = 8000

_LOOKBACK_RE = re.compile(r"^(\d+)(s|m|h|d)$")
_LOOKBACK_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _resolve_url() -> str:
    """Return the Prometheus base URL, honouring K8SENSE_PROM_URL env override."""
    return os.environ.get("K8SENSE_PROM_URL", DEFAULT_PROM_URL)


def _parse_lookback(lookback: str) -> int:
    """Convert '5m' / '1h' / '24h' to seconds. Raises ValueError on invalid input."""
    match = _LOOKBACK_RE.match(lookback)
    if not match:
        raise ValueError(f"invalid lookback: '{lookback}'")
    value, unit = match.groups()
    return int(value) * _LOOKBACK_SECONDS[unit]


def _compute_step(lookback_seconds: int) -> int:
    """Step size in seconds — floor at 15s, ensures ≤ 60 buckets per range query."""
    return max(15, lookback_seconds // 60)
