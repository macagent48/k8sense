"""SDK PreToolUse hook callback. Fetches pod status when needed; defers to safe_actions.decide."""

from __future__ import annotations

import shlex
from typing import Any, Callable

from k8sense.hooks.safe_actions import decide, parse_kubectl
from k8sense.permissions import PermissionMode
from k8sense.tools.kubectl import run_kubectl

_KUBECTL_TOOL_NAME = "mcp__k8sense__kubectl"


async def _fetch_pod_status(name: str, namespace: str) -> str | None:
    """Return the pod's status.phase via kubectl, or None if it can't be determined.

    A None return triggers the fail-closed branch in safe_actions.decide().
    """
    result = await run_kubectl(
        [
            "get",
            "pod",
            name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.status.phase}",
        ]
    )
    if result["exit_code"] != 0:
        return None
    phase = result["stdout"].strip()
    return phase or None


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

        pod_status: str | None = None
        if (
            invocation.verb == "delete"
            and invocation.resource_kind in {"pod", "pods"}
            and invocation.name
        ):
            pod_status = await _fetch_pod_status(
                invocation.name, invocation.namespace or "default"
            )

        decision = decide(invocation, mode, pod_status=pod_status)

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
