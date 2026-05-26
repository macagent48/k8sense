"""Pure signature extraction from a completed investigation, or hints from a question."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

# Most specific first; first match wins.
_REASON_PATTERNS = (
    "OOMKilled",
    "ImagePullBackOff",
    "ErrImagePull",
    "CrashLoopBackOff",
    "ContainerCreating",
    "Evicted",
    "NodeNotReady",
    "FailedScheduling",
    "Unhealthy",
    "BackOff",
    "Pending",
)

_KIND_NORMALISE = {
    "pod": "Pod",
    "pods": "Pod",
    "po": "Pod",
    "deployment": "Deployment",
    "deployments": "Deployment",
    "deploy": "Deployment",
    "node": "Node",
    "nodes": "Node",
    "no": "Node",
}


@dataclass(frozen=True)
class Signature:
    kind: str | None
    namespace: str | None
    name: str | None
    reason: str | None

    def is_empty(self) -> bool:
        return not any((self.kind, self.namespace, self.name, self.reason))


def _scan_for_reason(haystacks: list[str]) -> str | None:
    for pattern in _REASON_PATTERNS:
        for h in haystacks:
            if pattern in h:
                return pattern
    return None


def _extract_resource_target(
    tool_calls: list[dict],
) -> tuple[str | None, str | None, str | None]:
    """Find the (kind, namespace, name) most discussed in describe/logs invocations."""
    counter: Counter[tuple[str, str | None, str | None]] = Counter()

    for call in tool_calls:
        args = (call.get("input") or {}).get("args", [])
        if not args:
            continue
        verb = args[0]
        if verb not in {"describe", "logs"}:
            continue

        # logs: args[1] is the pod name
        # describe: args[1] is the kind, args[2] is the name
        kind_raw: str | None = None
        name: str | None = None
        namespace: str | None = None

        if verb == "describe" and len(args) >= 3:
            kind_raw = args[1]
            name = args[2]
        elif verb == "logs" and len(args) >= 2:
            kind_raw = "pod"
            name = args[1]

        # find -n or --namespace
        i = 0
        while i < len(args):
            if args[i] == "-n" and i + 1 < len(args):
                namespace = args[i + 1]
                break
            if args[i].startswith("--namespace"):
                if "=" in args[i]:
                    namespace = args[i].split("=", 1)[1]
                elif i + 1 < len(args):
                    namespace = args[i + 1]
                break
            i += 1

        if kind_raw and name:
            kind = _KIND_NORMALISE.get(kind_raw, kind_raw)
            counter[(kind, namespace, name)] += 1

    if not counter:
        return (None, None, None)
    (kind, namespace, name), _count = counter.most_common(1)[0]
    return (kind, namespace, name)


def extract(
    tool_calls: list[dict],
    tool_results: list[dict],
    final_text: str,
) -> Signature:
    """Best-effort signature from a completed investigation."""
    kind, namespace, name = _extract_resource_target(tool_calls)
    haystacks = [final_text] + [
        r.get("text", "") for r in tool_results if isinstance(r, dict)
    ]
    reason = _scan_for_reason(haystacks)
    return Signature(kind=kind, namespace=namespace, name=name, reason=reason)


def extract_text_hints(question: str) -> Signature:
    """Best-effort guess at signature from the question text alone.

    Returns whatever can be derived. In Phase 4 we only scan for reason keywords;
    namespace/name inference from raw question text would require topology lookup
    or NER and is deferred to Phase 4.1+.
    """
    reason = _scan_for_reason([question])
    return Signature(kind=None, namespace=None, name=None, reason=reason)
