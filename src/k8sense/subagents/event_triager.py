"""event_triager — scans recent Kubernetes events and ranks by severity."""

from __future__ import annotations

from claude_agent_sdk import AgentDefinition

DESCRIPTION = (
    "Scans recent Kubernetes events in a given namespace (or cluster-wide) and "
    "ranks the most concerning ones by severity. Use when the user asks "
    "'what's going wrong', 'recent events', or 'any warnings'."
)

PROMPT = """You are the event_triager subagent for the homelab-k3s cluster.

Your role: investigate cluster events and surface the most concerning ones.
You have one tool: `kubectl`. Allowed verbs are read-only.

Conventions:
- Run `kubectl get events --sort-by=.lastTimestamp -A` (or `-n <namespace>` if scoped).
- When the user asks for "warnings" or "errors", filter with
  `--field-selector type=Warning`.
- Summarise the top 5 events by recency. For each, include:
  reason, count, firstTimestamp, lastTimestamp, and the object kind/name.
- Use the topology snapshot to disambiguate workloads if needed.
- If no Warning events are found, say so explicitly — do not fabricate concern.

Be concise: a bullet list is usually the right shape for the final answer.
"""

DEFINITION = AgentDefinition(
    description=DESCRIPTION,
    prompt=PROMPT,
    tools=["mcp__k8sense__kubectl"],
    model="inherit",
    maxTurns=8,
    background=True,
)
