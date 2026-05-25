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


from k8sense.tools.prometheus import _format_result, _render_metric  # noqa: E402


def test_render_metric_includes_labels():
    rendered = _render_metric({"__name__": "node_load1", "instance": "master:9100"})
    assert "node_load1" in rendered
    assert 'instance="master:9100"' in rendered


def test_render_metric_handles_empty():
    assert _render_metric({}) == "{}"


def test_format_vector_result_includes_value_and_timestamp():
    data = {
        "resultType": "vector",
        "result": [
            {
                "metric": {"__name__": "node_load1", "instance": "master"},
                "value": [1234567890, "0.42"],
            },
        ],
    }
    output = _format_result(data)
    assert "resultType=vector" in output
    assert "count=1" in output
    assert "node_load1" in output
    assert "0.42" in output


def test_format_matrix_result_summarises_points():
    data = {
        "resultType": "matrix",
        "result": [
            {
                "metric": {"__name__": "node_load1"},
                "values": [
                    [1, "0.1"],
                    [2, "0.2"],
                    [3, "0.3"],
                    [4, "0.4"],
                    [5, "0.5"],
                    [6, "0.6"],
                ],
            },
        ],
    }
    output = _format_result(data)
    assert "resultType=matrix" in output
    assert "6 points" in output  # total
    # first 5 should appear, not all 6
    assert "[6, '0.6']" not in output or "first 5" in output


def test_format_result_truncates_when_too_many_lines():
    data = {
        "resultType": "vector",
        "result": [
            {"metric": {"__name__": "x", "i": str(i)}, "value": [0, "1"]}
            for i in range(100)
        ],
    }
    output = _format_result(data)
    assert "truncated" in output
    assert output.count("\n") < 55  # well under MAX_RESULT_LINES


import os  # noqa: E402

from k8sense.tools.prometheus import run_prometheus_query  # noqa: E402


@pytest.mark.asyncio
async def test_run_query_returns_error_when_prometheus_unreachable(monkeypatch):
    # Point at a deliberately-invalid address — no mocking, just env redirection.
    monkeypatch.setenv("K8SENSE_PROM_URL", "http://127.0.0.1:1")
    result = await run_prometheus_query("up")
    assert result["exit_code"] == -1
    assert (
        "unreachable" in result["stderr"].lower()
        or "connect" in result["stderr"].lower()
    )


@pytest.mark.asyncio
async def test_run_query_returns_error_for_invalid_lookback():
    result = await run_prometheus_query("up", lookback="5xyz")
    assert result["exit_code"] == -1
    assert "invalid lookback" in result["stderr"]


_REAL_PROM = os.environ.get("K8SENSE_PROM_URL_FOR_TESTS", "http://192.168.70.174:9090")


def _prom_reachable() -> bool:
    """Quick TCP probe — used to skip live tests when Prom is down."""
    import socket
    from urllib.parse import urlparse

    p = urlparse(_REAL_PROM)
    try:
        with socket.create_connection((p.hostname, p.port or 9090), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _prom_reachable(), reason="Prometheus not reachable")
@pytest.mark.asyncio
async def test_instant_query_against_real_prometheus(monkeypatch):
    monkeypatch.setenv("K8SENSE_PROM_URL", _REAL_PROM)
    # 'up' is the simplest universally-available metric
    result = await run_prometheus_query("up")
    assert result["exit_code"] == 0, result["stderr"]
    assert "resultType=vector" in result["stdout"]


@pytest.mark.skipif(not _prom_reachable(), reason="Prometheus not reachable")
@pytest.mark.asyncio
async def test_range_query_against_real_prometheus(monkeypatch):
    monkeypatch.setenv("K8SENSE_PROM_URL", _REAL_PROM)
    result = await run_prometheus_query("up", lookback="5m")
    assert result["exit_code"] == 0, result["stderr"]
    assert "resultType=matrix" in result["stdout"]


@pytest.mark.asyncio
async def test_run_query_returns_error_when_response_is_not_json(monkeypatch):
    """A 200 response with a non-JSON body must not crash the envelope contract."""
    # We can't easily get Prometheus itself to return non-JSON, but we can simulate
    # a proxy that returns HTML by pointing K8SENSE_PROM_URL at a small server.
    # The simplest way without bringing in mocks: monkeypatch httpx.AsyncClient.get
    # via a class replacement that returns a fake 200 + non-JSON body.
    from k8sense.tools import prometheus as prom_mod

    class _FakeResponse:
        status_code = 200
        text = "<html>not prometheus</html>"

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(prom_mod.httpx, "AsyncClient", _FakeClient)

    result = await prom_mod.run_prometheus_query("up")
    assert result["exit_code"] == -1
    assert "non-JSON" in result["stderr"]
    assert "<html>" in result["stderr"]
