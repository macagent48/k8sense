"""MCP server assembly — build_server + tool registration via registry."""

import pytest

from k8sense.mcp_server.server import build_server


def test_build_server_returns_a_server_instance():
    from mcp.server.lowlevel.server import Server

    server = build_server()
    assert isinstance(server, Server)


def test_build_server_registers_all_required_handlers():
    from mcp import types

    server = build_server()
    expected = [
        types.ListToolsRequest,
        types.CallToolRequest,
        types.ListResourcesRequest,
        types.ListResourceTemplatesRequest,
        types.ReadResourceRequest,
        types.ListPromptsRequest,
        types.GetPromptRequest,
    ]
    for handler_type in expected:
        assert handler_type in server.request_handlers, (
            f"missing handler for {handler_type.__name__}"
        )


@pytest.mark.asyncio
async def test_call_tool_dispatches_kubectl_via_registry():
    """A call_tool request for `kubectl` with a disallowed verb should reach the
    real handler and come back as a content-block list with the rejection text."""
    server = build_server()
    from mcp import types

    # Find the registered call_tool handler
    handler = server.request_handlers[types.CallToolRequest]
    # Build a minimal request envelope
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="kubectl",
            arguments={"args": ["delete", "pod", "x"]},
        ),
    )
    result = await handler(req)
    # The high-level Server wraps the content in a ServerResult; pull out the content list
    payload = result.root.content if hasattr(result, "root") else result.content
    assert any(
        "not allowed" in block.text for block in payload if hasattr(block, "text")
    )
