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
