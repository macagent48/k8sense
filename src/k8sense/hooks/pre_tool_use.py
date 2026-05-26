"""SDK PreToolUse hook callback. Fetches pod status when needed; defers to safe_actions.decide."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Callable

from k8sense.hooks.safe_actions import decide, is_recent_pending, parse_kubectl
from k8sense.permissions import PermissionMode
from k8sense.tools.kubectl import run_kubectl

_KUBECTL_TOOL_NAME = "mcp__k8sense__kubectl"


@dataclass(frozen=True)
class PodStatus:
    phase: str | None
    age_seconds: int | None  # None if can't be determined


async def _fetch_pod_status(name: str, namespace: str) -> PodStatus:
    """Return the pod's status.phase AND age via kubectl.

    Returns PodStatus(None, None) if the pod can't be queried at all.
    Returns PodStatus(phase, None) if phase was determinable but age wasn't.
    """
    result = await run_kubectl(
        [
            "get",
            "pod",
            name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.status.phase}|{.metadata.creationTimestamp}",
        ]
    )
    if result["exit_code"] != 0:
        return PodStatus(phase=None, age_seconds=None)
    text = result["stdout"].strip()
    if not text:
        return PodStatus(phase=None, age_seconds=None)
    phase_str, _, creation_iso = text.partition("|")
    phase = phase_str.strip() or None

    age_seconds: int | None = None
    if creation_iso:
        try:
            from datetime import datetime, timezone

            creation_dt = datetime.fromisoformat(creation_iso.replace("Z", "+00:00"))
            age_seconds = int(
                (datetime.now(timezone.utc) - creation_dt).total_seconds()
            )
        except (ValueError, TypeError):
            age_seconds = None

    return PodStatus(phase=phase, age_seconds=age_seconds)


def build_pre_tool_use_hook(
    mode: PermissionMode,
    on_propose: Callable[[str, str], None] | None = None,
):
    """Return an async hook callback closed over `mode` and the propose sink.

    on_propose: invoked with (command_string, decision.message) when a mutation
    is intercepted in propose mode. CLI plugs the renderer in here; Phase 5
    sentinel will plug Telegram in here.
    """

    async def hook(
        input_: dict[str, Any],
        tool_use_id: str | None,
        ctx: Any,
    ) -> dict[str, Any]:
        if input_.get("tool_name") != _KUBECTL_TOOL_NAME:
            return {}

        args = input_.get("tool_input", {}).get("args", [])
        invocation = parse_kubectl(args)

        status_info: PodStatus = PodStatus(phase=None, age_seconds=None)
        if (
            invocation.verb == "delete"
            and invocation.resource_kind in {"pod", "pods"}
            and invocation.name
        ):
            status_info = await _fetch_pod_status(
                invocation.name, invocation.namespace or "default"
            )

        # Special-case: Pending pods younger than 5 minutes are not allowlisted —
        # they're probably just starting up. The hook downgrades the status to None
        # (which decide() treats as "status unknown" → deny).
        effective_phase = status_info.phase
        if is_recent_pending(status_info.phase, status_info.age_seconds):
            effective_phase = None

        decision = decide(invocation, mode, pod_status=effective_phase)

        if decision.behaviour == "allow":
            return {}

        if decision.behaviour == "propose":
            command = "kubectl " + " ".join(shlex.quote(a) for a in args)
            if on_propose is not None:
                on_propose(command, decision.message)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Proposed (not executed in propose mode): {command}"
                    ),
                }
            }

        # deny
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.message,
            }
        }

    return hook
