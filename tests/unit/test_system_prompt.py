"""System prompt assembly: template + topology snapshot injection."""


from k8sense.prompts.system import build_system_prompt_from_topology


def test_prompt_includes_homelab_framing():
    prompt = build_system_prompt_from_topology("NAMESPACE\nargocd\nlonghorn\n")
    assert "k8sense" in prompt.lower()
    assert "homelab" in prompt.lower()
    assert "SRE" in prompt or "sre" in prompt.lower()


def test_prompt_includes_topology_snapshot():
    topology = "NAMESPACE   STATUS   AGE\nargocd      Active   1d\n"
    prompt = build_system_prompt_from_topology(topology)
    assert topology in prompt


def test_prompt_instructs_investigation_before_concluding():
    prompt = build_system_prompt_from_topology("")
    assert "investigate" in prompt.lower()


def test_prompt_lists_allowed_kubectl_verbs():
    prompt = build_system_prompt_from_topology("")
    # All read-only verbs should be mentioned so the model knows what's available
    for verb in ["get", "describe", "logs", "top", "events"]:
        assert verb in prompt
