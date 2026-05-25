"""The handler turns run_kubectl's dict into SDK content."""

import pytest

from k8sense.tools.kubectl import kubectl_handler


@pytest.mark.asyncio
async def test_handler_returns_sdk_content_block_for_rejected_verb():
    result = await kubectl_handler({"args": ["delete", "pod", "x"]})
    assert "content" in result
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block["type"] == "text"
    assert "not allowed" in block["text"]
    assert "exit_code=-1" in block["text"]


import shutil  # noqa: E402


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.asyncio
async def test_handler_returns_content_block_for_successful_call():
    result = await kubectl_handler({"args": ["version", "--client"]})
    assert "content" in result
    block = result["content"][0]
    assert "exit_code=0" in block["text"]
    # On success, stderr is empty so the stderr section should be absent.
    assert "--- stderr ---" not in block["text"]
