"""Behaviour of run_kubectl: allowlist rejection, success, timeout."""

import shutil

import pytest

from k8sense.tools.kubectl import run_kubectl


@pytest.mark.asyncio
async def test_disallowed_verb_returns_error_without_running_subprocess():
    result = await run_kubectl(["delete", "pod", "x"])
    assert result["exit_code"] == -1
    assert "not allowed" in result["stderr"]
    assert result["stdout"] == ""


@pytest.mark.asyncio
async def test_empty_args_returns_error():
    result = await run_kubectl([])
    assert result["exit_code"] == -1
    assert "not allowed" in result["stderr"]


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.asyncio
async def test_version_client_succeeds_without_cluster():
    result = await run_kubectl(["version", "--client", "-o", "yaml"])
    assert result["exit_code"] == 0
    assert "clientVersion" in result["stdout"]


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.asyncio
async def test_timeout_returns_error():
    # Force timeout by setting an absurdly small budget on a real-but-slow call.
    # `kubectl version --client` exits in <1s normally; 0.001s ensures timeout.
    result = await run_kubectl(["version", "--client"], timeout=0.001)
    assert result["exit_code"] == -1
    assert "timeout" in result["stderr"].lower()


@pytest.mark.asyncio
async def test_kubectl_missing_from_path_returns_error(monkeypatch, tmp_path):
    # Simulate kubectl not being installed by pointing PATH at an empty dir.
    # This is environment manipulation, not mocking — the subprocess primitive
    # genuinely can't find kubectl.
    monkeypatch.setenv("PATH", str(tmp_path))
    result = await run_kubectl(["get", "pods"])
    assert result["exit_code"] == -1
    assert "kubectl" in result["stderr"].lower()
    assert "not found" in result["stderr"].lower()
