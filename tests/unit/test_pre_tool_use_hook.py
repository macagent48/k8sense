"""The async PreToolUse hook callback returned by build_pre_tool_use_hook."""

import pytest

from k8sense.hooks.pre_tool_use import build_pre_tool_use_hook
from k8sense.permissions import PermissionMode


def _input(args):
    """Build a minimal PreToolUseHookInput-shaped dict."""
    return {
        "session_id": "s",
        "transcript_path": "/tmp/t",
        "cwd": "/tmp",
        "agent_id": "a",
        "agent_type": "main",
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__k8sense__kubectl",
        "tool_input": {"args": args},
        "tool_use_id": "u",
    }


@pytest.mark.asyncio
async def test_hook_passes_through_non_kubectl_tools():
    hook = build_pre_tool_use_hook(PermissionMode.READONLY)
    input_ = _input(["delete", "pod", "x", "-n", "argocd"]).copy()
    input_["tool_name"] = "mcp__k8sense__prometheus_query"
    result = await hook(input_, "u", None)
    assert result == {}  # empty = let it through


@pytest.mark.asyncio
async def test_hook_allows_read_only_kubectl():
    hook = build_pre_tool_use_hook(PermissionMode.READONLY)
    result = await hook(_input(["get", "pods", "-n", "argocd"]), "u", None)
    assert result == {}


@pytest.mark.asyncio
async def test_hook_denies_mutation_in_readonly(monkeypatch, tmp_path):
    # Hide kubectl so the hook's _fetch_pod_status returns None (cluster unreachable simulation)
    monkeypatch.setenv("PATH", str(tmp_path))
    hook = build_pre_tool_use_hook(PermissionMode.READONLY)
    result = await hook(_input(["delete", "pod", "x", "-n", "argocd"]), "u", None)
    assert "hookSpecificOutput" in result
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_hook_denies_non_allowlisted_in_autosafe(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    hook = build_pre_tool_use_hook(PermissionMode.AUTO_SAFE)
    # `apply` is never allowlisted
    result = await hook(_input(["apply", "-f", "manifest.yaml"]), "u", None)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        "not in safe-action allowlist"
        in result["hookSpecificOutput"]["permissionDecisionReason"]
    )


@pytest.mark.asyncio
async def test_hook_propose_calls_on_propose_callback(monkeypatch, tmp_path):
    """In propose mode, an allowlisted mutation is denied AND the on_propose sink fires."""
    # We can't easily fake an unhealthy pod status here, so use the cleanup variant
    # which doesn't need a status precondition.
    monkeypatch.setenv("PATH", str(tmp_path))
    captured: list[tuple[str, str]] = []
    hook = build_pre_tool_use_hook(
        PermissionMode.PROPOSE,
        on_propose=lambda cmd, msg: captured.append((cmd, msg)),
    )
    result = await hook(
        _input(
            [
                "delete",
                "pod",
                "--field-selector=status.phase=Succeeded",
                "-n",
                "argocd",
            ]
        ),
        "u",
        None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Proposed" in result["hookSpecificOutput"]["permissionDecisionReason"]
    assert len(captured) == 1
    cmd, _msg = captured[0]
    assert cmd.startswith("kubectl delete pod ")
    assert "Succeeded" in cmd


@pytest.mark.asyncio
async def test_hook_allows_cleanup_in_autosafe(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    hook = build_pre_tool_use_hook(PermissionMode.AUTO_SAFE)
    result = await hook(
        _input(
            [
                "delete",
                "pod",
                "--field-selector=status.phase=Succeeded",
                "-n",
                "argocd",
            ]
        ),
        "u",
        None,
    )
    assert result == {}  # allow
