"""Prometheus tool: URL resolution, lookback parsing, step computation."""

import pytest

from k8sense.tools.prometheus import (
    DEFAULT_PROM_URL,
    _compute_step,
    _parse_lookback,
    _resolve_url,
)


def test_default_prom_url_is_homelab_address():
    assert DEFAULT_PROM_URL == "http://192.168.70.174:9090"


def test_resolve_url_returns_default_without_env(monkeypatch):
    monkeypatch.delenv("K8SENSE_PROM_URL", raising=False)
    assert _resolve_url() == DEFAULT_PROM_URL


def test_resolve_url_honours_env_override(monkeypatch):
    monkeypatch.setenv("K8SENSE_PROM_URL", "http://prom.example.com:9090")
    assert _resolve_url() == "http://prom.example.com:9090"


@pytest.mark.parametrize(
    "lookback,expected_seconds",
    [
        ("30s", 30),
        ("5m", 300),
        ("1h", 3600),
        ("24h", 86400),
        ("2d", 172800),
    ],
)
def test_parse_lookback_valid(lookback, expected_seconds):
    assert _parse_lookback(lookback) == expected_seconds


@pytest.mark.parametrize("bad", ["5", "5x", "abc", "", "5min", "h1"])
def test_parse_lookback_rejects_invalid(bad):
    with pytest.raises(ValueError, match="invalid lookback"):
        _parse_lookback(bad)


def test_compute_step_caps_at_60_buckets():
    # 1h = 3600s ⇒ step ≥ 60s (so ≤ 60 buckets)
    assert _compute_step(3600) == 60


def test_compute_step_floor_is_15s():
    # 1m of data shouldn't produce a 1s step
    assert _compute_step(60) == 15
