"""MCP resources — content helpers, namespace validation, URI dispatch."""

import pytest

from k8sense.mcp_server.resources import (
    _is_valid_namespace,
    _manifests_content,
    _recent_events_content,
    _topology_content,
)


@pytest.mark.parametrize(
    "ns", ["argocd", "kube-system", "longhorn-system", "a", "abc-123"]
)
def test_namespace_validation_accepts_dns1123(ns):
    assert _is_valid_namespace(ns) is True


@pytest.mark.parametrize(
    "ns",
    [
        "",  # empty
        "Argocd",  # uppercase
        "argo_cd",  # underscore
        "--all",  # leading dashes
        "ns space",  # space
        "../etc",  # path traversal
        "a" * 64,  # over 63 chars
    ],
)
def test_namespace_validation_rejects_invalid(ns):
    assert _is_valid_namespace(ns) is False


@pytest.mark.asyncio
async def test_topology_content_succeeds_with_real_cluster():
    # Real cluster reachable in this dev environment. The body contains the markdown
    # heading and a fenced code block.
    body = await _topology_content()
    assert body.startswith("# Cluster topology")
    assert "```" in body


@pytest.mark.asyncio
async def test_topology_content_returns_error_body_when_kubectl_missing(
    monkeypatch, tmp_path
):
    # PATH manipulation hides kubectl; the helper returns a markdown error body, not an exception.
    monkeypatch.setenv("PATH", str(tmp_path))
    body = await _topology_content()
    assert body.startswith("# Topology fetch failed")
    assert "stderr" in body


@pytest.mark.asyncio
async def test_manifests_content_rejects_invalid_namespace_without_calling_kubectl():
    body = await _manifests_content("../etc")
    assert body.startswith("# Invalid namespace")
    assert "DNS-1123" in body


@pytest.mark.asyncio
async def test_manifests_content_succeeds_for_valid_namespace():
    body = await _manifests_content("kube-system")
    # The kube-system namespace exists and `get all` returns a YAML block.
    assert body.startswith("# Manifests in `kube-system`")


@pytest.mark.asyncio
async def test_recent_events_content_returns_markdown_with_code_fence():
    body = await _recent_events_content()
    # Whether or not there are events, the body is a markdown doc.
    assert body.startswith("# Recent Warning events")
    assert "```" in body


@pytest.mark.asyncio
async def test_recent_events_content_caps_at_30_lines():
    body = await _recent_events_content()
    # Count non-fence lines inside the body; the cap should keep it small.
    in_fence = False
    payload_line_count = 0
    for line in body.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            payload_line_count += 1
    # The actual cap is 30; allow some margin since the kubectl output may
    # contain header lines that count.
    assert payload_line_count <= 32, (
        f"expected ≤32 lines inside fence, got {payload_line_count}"
    )


from mcp.server.lowlevel.server import Server  # noqa: E402

from k8sense.mcp_server.resources import register_resources  # noqa: E402


def _build_server_with_resources() -> Server:
    server = Server("k8sense-test")
    register_resources(server)
    return server


def test_register_resources_attaches_list_resources_handler():
    server = _build_server_with_resources()
    # mcp.server.Server stores handlers in `request_handlers` keyed by request type.
    from mcp import types

    assert types.ListResourcesRequest in server.request_handlers
    assert types.ListResourceTemplatesRequest in server.request_handlers
    assert types.ReadResourceRequest in server.request_handlers


@pytest.mark.asyncio
async def test_read_resource_routes_topology(monkeypatch, tmp_path):
    # Hide kubectl so the content fetch fails predictably — we just want to confirm routing.
    monkeypatch.setenv("PATH", str(tmp_path))
    server = _build_server_with_resources()
    from mcp import types
    from pydantic import AnyUrl

    handler = server.request_handlers[types.ReadResourceRequest]
    req = types.ReadResourceRequest(
        method="resources/read",
        params=types.ReadResourceRequestParams(uri=AnyUrl("mcp://k8sense/topology")),
    )
    result = await handler(req)
    contents = result.root.contents if hasattr(result, "root") else result.contents
    assert any("Topology" in c.text for c in contents)


@pytest.mark.asyncio
async def test_read_resource_routes_manifests_template(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    server = _build_server_with_resources()
    from mcp import types
    from pydantic import AnyUrl

    handler = server.request_handlers[types.ReadResourceRequest]
    req = types.ReadResourceRequest(
        method="resources/read",
        params=types.ReadResourceRequestParams(
            uri=AnyUrl("mcp://k8sense/manifests/argocd")
        ),
    )
    result = await handler(req)
    contents = result.root.contents if hasattr(result, "root") else result.contents
    # kubectl is hidden, so the body is the "fetch failed" markdown for argocd
    assert any("argocd" in c.text for c in contents)


@pytest.mark.asyncio
async def test_read_resource_unknown_uri_raises():
    server = _build_server_with_resources()
    from mcp import types
    from pydantic import AnyUrl

    handler = server.request_handlers[types.ReadResourceRequest]
    req = types.ReadResourceRequest(
        method="resources/read",
        params=types.ReadResourceRequestParams(uri=AnyUrl("mcp://k8sense/nope")),
    )
    with pytest.raises(
        Exception
    ):  # ValueError is wrapped by the MCP layer; either way it raises
        await handler(req)
