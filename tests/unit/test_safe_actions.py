"""Tests for hooks.safe_actions: parse_kubectl, is_allowlisted, decide."""


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
