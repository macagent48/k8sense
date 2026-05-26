"""Pure logic for the PreToolUse hook.

parse_kubectl: structured form of kubectl argv
is_allowlisted: True iff invocation matches one of the four safe actions
decide: truth table allow / deny / propose given (invocation, mode, pod_status)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from k8sense.permissions import PermissionMode

_KIND_ALIASES = {
    "pod": "pod",
    "pods": "pods",
    "po": "pod",
    "deployment": "deployment",
    "deployments": "deployments",
    "deploy": "deployment",
    "node": "node",
    "nodes": "nodes",
    "no": "node",
}


@dataclass(frozen=True)
class KubectlInvocation:
    verb: str
    args: list[str]
    resource_kind: str | None
    name: str | None
    namespace: str | None
    flags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    behaviour: Literal["allow", "deny", "propose"]
    message: str


def _parse_flags_and_positionals(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Split `args` into positionals and a flag dict.

    Handles --flag=value, --flag value, and bare --flag (mapped to "").
    The -n shorthand and --namespace are both treated as flags.
    """
    positionals: list[str] = []
    flags: dict[str, str] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("--"):
            if "=" in token:
                key, _, value = token[2:].partition("=")
                flags[key] = value
            else:
                key = token[2:]
                if i + 1 < len(args) and not args[i + 1].startswith("-"):
                    flags[key] = args[i + 1]
                    i += 1
                else:
                    flags[key] = ""
        elif token == "-n" and i + 1 < len(args):
            flags["namespace"] = args[i + 1]
            i += 1
        else:
            positionals.append(token)
        i += 1
    return positionals, flags


def parse_kubectl(args: list[str]) -> KubectlInvocation:
    """Best-effort parse of kubectl argv. Unknown shapes return verb='<unknown>'."""
    if not args:
        return KubectlInvocation(
            verb="<unknown>", args=[], resource_kind=None, name=None, namespace=None
        )

    positionals, flags = _parse_flags_and_positionals(args)
    verb = positionals[0] if positionals else "<unknown>"
    namespace = flags.get("namespace")

    resource_kind: str | None = None
    name: str | None = None

    # Shape A: <verb> <kind> [name] ...
    if len(positionals) >= 2:
        kind_token = positionals[1]
        # Shape B: <verb> kind/name (e.g. rollout restart deployment/argocd-server)
        if "/" in kind_token:
            kind_part, _, name_part = kind_token.partition("/")
            resource_kind = _KIND_ALIASES.get(kind_part, kind_part)
            name = name_part or None
        else:
            resource_kind = _KIND_ALIASES.get(kind_token, kind_token)
            if len(positionals) >= 3:
                name = positionals[2]

    # Shape C: <verb> <node-name>  (cordon / drain / etc — name follows verb directly)
    # For these verbs the second positional is always the node name, never a resource kind.
    if verb in {"cordon", "drain", "uncordon"} and len(positionals) >= 2:
        resource_kind = "node"
        name = positionals[1]

    # Shape D: rollout restart deployment/<name> -n <ns>
    if verb == "rollout" and len(positionals) >= 3:
        # positionals are: rollout, restart, deployment/name
        subverb_token = positionals[1]  # "restart"
        target_token = positionals[2]  # "deployment/argocd-server"
        if "/" in target_token:
            kind_part, _, name_part = target_token.partition("/")
            resource_kind = _KIND_ALIASES.get(kind_part, kind_part)
            name = name_part or None

    return KubectlInvocation(
        verb=verb,
        args=args,
        resource_kind=resource_kind,
        name=name,
        namespace=namespace,
        flags=flags,
    )


_UNHEALTHY_STATUSES = frozenset(
    {
        "CrashLoopBackOff",
        "ImagePullBackOff",
        "ErrImagePull",
        "Error",
        "Unknown",
        "Pending",
    }
)


def is_read_only(verb: str) -> bool:
    return verb in {"get", "describe", "logs", "top", "events", "version"}


def is_allowlisted(invocation: KubectlInvocation, pod_status: str | None) -> bool:
    """True iff this invocation matches one of the four safe actions.

    Action 1: delete pod <name> -n <ns>          [requires unhealthy pod_status]
    Action 2: rollout restart deployment/<name>  [no precondition]
    Action 3: cordon <node>                      [no precondition]
    Action 4: delete pod --field-selector=status.phase=Succeeded  [no precondition]
    """
    # Action 4 (must check before Action 1 to bypass status precondition)
    if (
        invocation.verb == "delete"
        and invocation.resource_kind in {"pod", "pods"}
        and invocation.flags.get("field-selector") == "status.phase=Succeeded"
    ):
        return True

    # Action 1
    if (
        invocation.verb == "delete"
        and invocation.resource_kind in {"pod", "pods"}
        and invocation.name
        and not invocation.flags.get("field-selector")  # not the cleanup variant
    ):
        return pod_status in _UNHEALTHY_STATUSES

    # Action 2
    if (
        invocation.verb == "rollout"
        and invocation.resource_kind in {"deployment", "deployments"}
        and "restart" in invocation.args
        and invocation.name
    ):
        return True

    # Action 3
    if (
        invocation.verb == "cordon"
        and invocation.resource_kind == "node"
        and invocation.name
    ):
        return True

    return False


def decide(
    invocation: KubectlInvocation,
    mode: PermissionMode,
    pod_status: str | None = None,
) -> Decision:
    """Pure dispatch table.

                       readonly      propose       auto-safe
    read-only verb     allow         allow         allow
    allowlisted mut    deny          propose       allow
    other mutation     deny          propose       deny
    """
    if is_read_only(invocation.verb):
        return Decision(behaviour="allow", message="read-only verb")

    allowlisted = is_allowlisted(invocation, pod_status)

    if mode == PermissionMode.READONLY:
        return Decision(
            behaviour="deny",
            message="mutations are blocked in readonly mode; re-run with --auto-fix or --propose",
        )

    if mode == PermissionMode.PROPOSE:
        return Decision(
            behaviour="propose",
            message=(
                "allowlisted mutation surfaced for review in propose mode"
                if allowlisted
                else (
                    f"verb {invocation.verb!r} is not in the safe-action allowlist "
                    "(propose mode shows what would run but does not gate safety); "
                    "use --auto-fix only for: delete pod / rollout restart deployment / "
                    "cordon node / delete pod (cleanup)"
                )
            ),
        )

    # AUTO_SAFE
    if allowlisted:
        return Decision(behaviour="allow", message="allowlisted in auto-safe mode")

    # Non-allowlisted mutation in auto-safe
    return Decision(
        behaviour="deny",
        message=(
            f"verb {invocation.verb!r} not in safe-action allowlist; "
            "only delete pod / rollout restart deployment / cordon node / "
            "delete pod (cleanup) are permitted in auto-safe mode"
        ),
    )
