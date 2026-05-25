"""The @tool-decorated wrapper turns run_kubectl's dict into SDK content."""

import pytest

from k8sense.tools.kubectl import kubectl_tool


@pytest.mark.asyncio
async def test_wrapper_returns_sdk_content_block_for_rejected_verb():
    result = await kubectl_tool({"args": ["delete", "pod", "x"]})
    assert "content" in result
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block["type"] == "text"
    assert "not allowed" in block["text"]
    assert "exit_code=-1" in block["text"]
