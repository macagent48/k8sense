"""MCP prompts — three workflow templates mirroring Phase 2 subagent playbooks."""

from __future__ import annotations

import re

# DNS-1123 label: lowercase alphanumeric and hyphens, 1-63 chars,
# must start and end with alphanumeric (rejects all-hyphens strings like "--all").
_NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _validate_namespace(ns: str) -> None:
    if not _NAMESPACE_RE.match(ns):
        raise ValueError(f"invalid namespace: {ns!r}")


def _investigate_pod(pod: str, namespace: str) -> str:
    _validate_namespace(namespace)
    return (
        f"Investigate pod `{pod}` in namespace `{namespace}` on the homelab-k3s cluster.\n\n"
        "Follow this playbook:\n"
        f"1. Read events and restart history: `kubectl describe pod {pod} -n {namespace}`\n"
        f"2. Tail current logs: `kubectl logs {pod} -n {namespace} --tail=200`\n"
        "3. If logs are empty, retry with `--previous` to see the prior container's logs.\n"
        "4. Recognise common patterns from describe output:\n"
        "   - `OOMKilled` → memory limit hit.\n"
        "   - `ImagePullBackOff` → image / registry / credentials issue.\n"
        "   - `CrashLoopBackOff` with empty current logs → check `--previous`.\n"
        "5. Quote 2-3 concrete log lines in your final answer instead of paraphrasing.\n\n"
        "Keep the final answer to one short paragraph plus the quoted log lines."
    )


def _triage_events(namespace: str | None) -> str:
    if namespace is not None:
        _validate_namespace(namespace)
    scope = (
        f"the `{namespace}` namespace" if namespace else "the cluster (all namespaces)"
    )
    selector = f"-n {namespace}" if namespace else "-A"
    return (
        f"Triage recent Kubernetes events in {scope} on the homelab-k3s cluster.\n\n"
        "Follow this playbook:\n"
        f"1. List recent Warning events: "
        f"`kubectl get events {selector} --sort-by=.lastTimestamp --field-selector=type=Warning`\n"
        "2. Summarise the top 5 by recency. For each include:\n"
        "   reason, count, firstTimestamp, lastTimestamp, and object kind/name.\n"
        "3. If no Warning events were found, say so explicitly — do not fabricate concern.\n\n"
        "Final answer should be a short bullet list."
    )


def _metrics(namespace: str, lookback: str | None) -> str:
    _validate_namespace(namespace)
    if lookback is None:
        return (
            f"Report current resource usage for namespace `{namespace}` on the homelab-k3s cluster.\n\n"
            f"Run: `kubectl top pods -n {namespace}`\n"
            "Summarise which pods are using the most CPU and memory. Quote concrete numbers."
        )
    return (
        f"Report resource usage trends for namespace `{namespace}` "
        f"over the last `{lookback}` on the homelab-k3s cluster.\n\n"
        "Use prometheus_query with these PromQL primitives:\n"
        f'- pod CPU rate: `sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[2m])) by (pod)`\n'
        f'- pod memory: `container_memory_working_set_bytes{{namespace="{namespace}"}}`\n\n'
        f"Pass `lookback={lookback}` for the range query. Summarise concrete numbers."
    )
