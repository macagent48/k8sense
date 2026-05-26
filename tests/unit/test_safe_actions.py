"""Tests for hooks.safe_actions: parse_kubectl, is_allowlisted, decide."""

import pytest

from k8sense.hooks.safe_actions import parse_kubectl


def test_parse_get_pods():
    inv = parse_kubectl(["get", "pods", "-n", "argocd"])
    assert inv.verb == "get"
    assert inv.resource_kind == "pods"
    assert inv.namespace == "argocd"
    assert inv.name is None


def test_parse_describe_pod_with_name():
    inv = parse_kubectl(["describe", "pod", "argocd-server-7d", "-n", "argocd"])
    assert inv.verb == "describe"
    assert inv.resource_kind == "pod"
    assert inv.name == "argocd-server-7d"
    assert inv.namespace == "argocd"


def test_parse_delete_pod():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    assert inv.verb == "delete"
    assert inv.resource_kind == "pod"
    assert inv.name == "x"
    assert inv.namespace == "argocd"


def test_parse_rollout_restart_deployment_slash_name():
    inv = parse_kubectl(
        ["rollout", "restart", "deployment/argocd-server", "-n", "argocd"]
    )
    assert inv.verb == "rollout"
    assert inv.resource_kind == "deployment"
    assert inv.name == "argocd-server"
    assert inv.namespace == "argocd"
    # We can also peek at the rollout subverb via flags or args
    assert "restart" in inv.args


def test_parse_cordon_node():
    inv = parse_kubectl(["cordon", "worker1"])
    assert inv.verb == "cordon"
    assert inv.resource_kind == "node"
    assert inv.name == "worker1"
    assert inv.namespace is None


def test_parse_delete_with_field_selector():
    inv = parse_kubectl(
        ["delete", "pod", "--field-selector=status.phase=Succeeded", "-n", "argocd"]
    )
    assert inv.verb == "delete"
    assert inv.resource_kind == "pod"
    # name is absent because no positional after pod
    assert inv.name is None
    assert inv.namespace == "argocd"
    assert inv.flags.get("field-selector") == "status.phase=Succeeded"


def test_parse_namespace_via_dash_dash_namespace():
    inv = parse_kubectl(["get", "pods", "--namespace=longhorn-system"])
    assert inv.namespace == "longhorn-system"


def test_parse_namespace_via_dash_dash_namespace_space():
    inv = parse_kubectl(["get", "pods", "--namespace", "longhorn-system"])
    assert inv.namespace == "longhorn-system"


def test_parse_empty_args_returns_unknown():
    inv = parse_kubectl([])
    assert inv.verb == "<unknown>"


def test_parse_unknown_verb_keeps_args():
    inv = parse_kubectl(["frobnicate", "pod", "x"])
    assert inv.verb == "frobnicate"
    # Best-effort: resource_kind may still be parsed


from k8sense.hooks.safe_actions import is_allowlisted, is_read_only  # noqa: E402


@pytest.mark.parametrize(
    "verb", ["get", "describe", "logs", "top", "events", "version"]
)
def test_read_only_verbs(verb):
    assert is_read_only(verb) is True


@pytest.mark.parametrize("verb", ["delete", "rollout", "cordon", "apply", "scale"])
def test_non_read_only_verbs(verb):
    assert is_read_only(verb) is False


def test_allowlist_delete_pod_with_unhealthy_status():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    assert is_allowlisted(inv, pod_status="CrashLoopBackOff") is True
    assert is_allowlisted(inv, pod_status="ImagePullBackOff") is True
    assert is_allowlisted(inv, pod_status="Error") is True
    assert is_allowlisted(inv, pod_status="Unknown") is True
    assert is_allowlisted(inv, pod_status="Pending") is True


def test_allowlist_delete_pod_rejects_healthy_status():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    assert is_allowlisted(inv, pod_status="Running") is False
    assert is_allowlisted(inv, pod_status="Succeeded") is False


def test_allowlist_delete_pod_rejects_unknown_status_fail_closed():
    """When status can't be determined, fail closed."""
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    assert is_allowlisted(inv, pod_status=None) is False


def test_allowlist_rollout_restart_deployment():
    inv = parse_kubectl(
        ["rollout", "restart", "deployment/argocd-server", "-n", "argocd"]
    )
    assert is_allowlisted(inv, pod_status=None) is True


def test_allowlist_cordon_node():
    inv = parse_kubectl(["cordon", "worker1"])
    assert is_allowlisted(inv, pod_status=None) is True


def test_allowlist_delete_pod_field_selector_succeeded():
    """Cleanup action: delete pods with --field-selector=status.phase=Succeeded."""
    inv = parse_kubectl(
        [
            "delete",
            "pod",
            "--field-selector=status.phase=Succeeded",
            "-n",
            "argocd",
        ]
    )
    # Status precondition doesn't apply when using field-selector cleanup
    assert is_allowlisted(inv, pod_status=None) is True


def test_allowlist_rejects_other_mutations():
    for argv in [
        ["apply", "-f", "manifest.yaml"],
        ["scale", "deployment/x", "--replicas=0", "-n", "argocd"],
        ["delete", "deployment", "x", "-n", "argocd"],
        ["edit", "pod", "x"],
        ["drain", "worker1"],
        ["delete", "job", "x", "-n", "argocd"],
    ]:
        inv = parse_kubectl(argv)
        assert is_allowlisted(inv, pod_status="CrashLoopBackOff") is False, (
            f"should reject: {argv}"
        )


from k8sense.hooks.safe_actions import decide  # noqa: E402
from k8sense.permissions import PermissionMode  # noqa: E402


def test_decide_read_only_always_allow():
    inv = parse_kubectl(["get", "pods"])
    for mode in PermissionMode:
        d = decide(inv, mode, pod_status=None)
        assert d.behaviour == "allow", f"{mode} should allow read-only"


def test_decide_allowlisted_mutation_under_readonly_is_deny():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    d = decide(inv, PermissionMode.READONLY, pod_status="CrashLoopBackOff")
    assert d.behaviour == "deny"
    assert d.message  # has a message


def test_decide_allowlisted_mutation_under_propose_is_propose():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    d = decide(inv, PermissionMode.PROPOSE, pod_status="CrashLoopBackOff")
    assert d.behaviour == "propose"


def test_decide_allowlisted_mutation_under_autosafe_is_allow():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    d = decide(inv, PermissionMode.AUTO_SAFE, pod_status="CrashLoopBackOff")
    assert d.behaviour == "allow"


def test_decide_non_allowlisted_mutation_always_deny():
    inv = parse_kubectl(["apply", "-f", "manifest.yaml"])
    for mode in PermissionMode:
        d = decide(inv, mode, pod_status=None)
        assert d.behaviour == "deny", f"{mode} should deny non-allowlisted mutation"


def test_decide_delete_pod_with_unknown_status_is_deny_in_autosafe():
    """Fail closed: status unknown → deny even in auto-safe."""
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    d = decide(inv, PermissionMode.AUTO_SAFE, pod_status=None)
    assert d.behaviour == "deny"


def test_decide_cleanup_pod_under_autosafe_is_allow_without_status():
    inv = parse_kubectl(
        [
            "delete",
            "pod",
            "--field-selector=status.phase=Succeeded",
            "-n",
            "argocd",
        ]
    )
    d = decide(inv, PermissionMode.AUTO_SAFE, pod_status=None)
    assert d.behaviour == "allow"
