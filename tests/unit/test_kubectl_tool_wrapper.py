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


@pytest.mark.asyncio
async def test_handler_output_format_is_stable_contract():
    """Pin the kubectl_handler envelope format. Agent's parse_handler_envelope
    relies on this exact structure: first line is $ kubectl ..., second line is
    exit_code=N, then --- stdout --- section, then optional --- stderr --- section.
    """
    result = await kubectl_handler({"args": ["delete", "pod", "x"]})
    text = result["content"][0]["text"]
    lines = text.splitlines()
    assert lines[0].startswith("$ kubectl ")
    assert lines[1].startswith("exit_code=")
    assert "--- stdout ---" in text
