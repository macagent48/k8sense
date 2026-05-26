"""MCP prompts — template assembly, namespace validation, dispatch."""

import pytest

from k8sense.mcp_server.prompts import (
    _investigate_pod,
    _metrics,
    _triage_events,
    _validate_namespace,
)


def test_validate_namespace_accepts_valid():
    # Should not raise
    _validate_namespace("argocd")
    _validate_namespace("kube-system")


@pytest.mark.parametrize("bad", ["", "Argocd", "argo_cd", "--all", "../etc"])
def test_validate_namespace_rejects_invalid(bad):
    with pytest.raises(ValueError, match="invalid namespace"):
        _validate_namespace(bad)


def test_investigate_pod_includes_pod_and_namespace_in_describe():
    text = _investigate_pod(pod="argocd-server-7d", namespace="argocd")
    assert "argocd-server-7d" in text
    assert "argocd" in text
    assert "kubectl describe pod argocd-server-7d -n argocd" in text


def test_investigate_pod_mentions_common_patterns():
    text = _investigate_pod(pod="x", namespace="argocd")
    assert "OOMKilled" in text
    assert "ImagePullBackOff" in text
    assert "CrashLoopBackOff" in text
    assert "--previous" in text


def test_investigate_pod_rejects_bad_namespace():
    with pytest.raises(ValueError):
        _investigate_pod(pod="x", namespace="../etc")


def test_triage_events_cluster_wide_when_no_namespace():
    text = _triage_events(namespace=None)
    assert "cluster (all namespaces)" in text
    assert "-A" in text


def test_triage_events_scoped_to_namespace():
    text = _triage_events(namespace="argocd")
    assert "argocd" in text
    assert "-n argocd" in text


def test_triage_events_rejects_bad_namespace():
    with pytest.raises(ValueError):
        _triage_events(namespace="--all")


def test_metrics_snapshot_when_no_lookback():
    text = _metrics(namespace="monitoring", lookback=None)
    assert "kubectl top pods -n monitoring" in text


def test_metrics_trend_when_lookback_provided():
    text = _metrics(namespace="monitoring", lookback="1h")
    assert "container_cpu_usage_seconds_total" in text
    assert "1h" in text


def test_metrics_rejects_bad_namespace():
    with pytest.raises(ValueError):
        _metrics(namespace="ns space", lookback=None)


from mcp.server.lowlevel.server import Server  # noqa: E402

from k8sense.mcp_server.prompts import register_prompts  # noqa: E402


def _build_server_with_prompts() -> Server:
    server = Server("k8sense-test")
    register_prompts(server)
    return server


def test_register_prompts_attaches_list_and_get_prompt_handlers():
    server = _build_server_with_prompts()
    from mcp import types

    assert types.ListPromptsRequest in server.request_handlers
    assert types.GetPromptRequest in server.request_handlers
