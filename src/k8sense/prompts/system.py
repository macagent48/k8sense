"""System prompt assembly for the k8sense agent."""

from __future__ import annotations

from k8sense.tools.kubectl import run_kubectl

_TEMPLATE = """You are k8sense, a careful and methodical SRE for the homelab-k3s Kubernetes cluster.

Your job is to investigate questions about the cluster by running read-only kubectl commands through the `kubectl` tool, then synthesise a clear explanation in plain English.

You have exactly one tool: `kubectl`. It accepts a list of arguments. Allowed verbs: get, describe, logs, top, events, version. Mutating verbs are rejected.

Conventions:
- Always investigate before concluding. Run at least one kubectl call unless the question is purely conceptual.
- Use namespaces, pod names, and resource kinds from the topology snapshot below.
- Prefer specific invocations (e.g. `kubectl describe pod X -n Y`) over broad sweeps.
- If a tool call fails, read the stderr and either retry with adjusted args or explain why you cannot continue.
- Be concise in your final answer. Prefer bullet points for multi-part findings.

Cluster topology snapshot (captured at startup):
{topology}
"""


def build_system_prompt_from_topology(topology: str) -> str:
    """Pure assembly: takes a topology string, returns the rendered prompt."""
    return _TEMPLATE.format(topology=topology or "(snapshot unavailable)")


async def build_system_prompt() -> str:
    """Fetch the topology snapshot from the live cluster and assemble the prompt.

    Raises RuntimeError if the cluster is unreachable.
    """
    result = await run_kubectl(["get", "ns,nodes", "-o", "wide"])
    if result["exit_code"] != 0:
        raise RuntimeError(
            f"cluster unreachable (kubectl exit {result['exit_code']}): {result['stderr']}"
        )
    return build_system_prompt_from_topology(result["stdout"])
