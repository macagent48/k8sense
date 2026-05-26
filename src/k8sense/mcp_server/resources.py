"""MCP resources: live topology, namespace manifests, recent Warning events."""

from __future__ import annotations

import re

from k8sense.tools.kubectl import run_kubectl

# DNS-1123 label: lowercase alphanumeric and hyphens, 1-63 chars,
# must start and end with an alphanumeric character.
_NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_EVENT_LINES_CAP = 30


def _is_valid_namespace(ns: str) -> bool:
    return bool(_NAMESPACE_RE.match(ns))


async def _topology_content() -> str:
    result = await run_kubectl(["get", "ns,nodes", "-o", "wide"])
    if result["exit_code"] != 0:
        return f"# Topology fetch failed\n\nstderr: {result['stderr']}"
    return f"# Cluster topology\n\n```\n{result['stdout']}\n```\n"


async def _manifests_content(namespace: str) -> str:
    if not _is_valid_namespace(namespace):
        return (
            f"# Invalid namespace\n\n"
            f"Namespace '{namespace}' is not a valid DNS-1123 label."
        )
    result = await run_kubectl(["get", "all", "-n", namespace, "-o", "yaml"])
    if result["exit_code"] != 0:
        return f"# Manifests fetch failed for {namespace}\n\nstderr: {result['stderr']}"
    return f"# Manifests in `{namespace}`\n\n```yaml\n{result['stdout']}\n```\n"


async def _recent_events_content() -> str:
    result = await run_kubectl(
        [
            "get",
            "events",
            "-A",
            "--field-selector=type=Warning",
            "--sort-by=.lastTimestamp",
        ]
    )
    if result["exit_code"] != 0:
        return f"# Recent events fetch failed\n\nstderr: {result['stderr']}"
    lines = result["stdout"].splitlines()
    body = (
        "\n".join(lines[-_EVENT_LINES_CAP:])
        if len(lines) > _EVENT_LINES_CAP
        else result["stdout"]
    )
    return f"# Recent Warning events\n\n```\n{body}\n```\n"


from mcp.server.lowlevel.server import Server  # noqa: E402
from mcp.types import Resource, ResourceTemplate  # noqa: E402
from pydantic import AnyUrl  # noqa: E402


def register_resources(server: Server) -> None:
    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri=AnyUrl("mcp://k8sense/topology"),
                name="Cluster topology",
                description="Current namespaces and nodes (kubectl get ns,nodes -o wide).",
                mimeType="text/markdown",
            ),
            Resource(
                uri=AnyUrl("mcp://k8sense/events/recent"),
                name="Recent Warning events",
                description=f"Last {_EVENT_LINES_CAP} cluster-wide Warning events.",
                mimeType="text/markdown",
            ),
        ]

    @server.list_resource_templates()
    async def list_resource_templates() -> list[ResourceTemplate]:
        return [
            ResourceTemplate(
                uriTemplate="mcp://k8sense/manifests/{namespace}",
                name="Namespace manifests",
                description="kubectl get all -n <namespace> -o yaml. Replace {namespace}.",
                mimeType="text/markdown",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> str:
        uri_str = str(uri)
        if uri_str == "mcp://k8sense/topology":
            return await _topology_content()
        if uri_str == "mcp://k8sense/events/recent":
            return await _recent_events_content()
        prefix = "mcp://k8sense/manifests/"
        if uri_str.startswith(prefix):
            ns = uri_str[len(prefix) :]
            return await _manifests_content(ns)
        raise ValueError(f"unknown resource: {uri_str}")
