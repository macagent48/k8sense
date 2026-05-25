"""System prompt assembly: template + topology snapshot injection."""

import pytest

from k8sense.prompts.system import (
    build_system_prompt,
    build_system_prompt_from_topology,
)


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
    for verb in ["get", "describe", "logs", "top", "events", "version"]:
        assert verb in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_raises_when_topology_fetch_fails(
    monkeypatch, tmp_path
):
    # Force run_kubectl to fail by hiding kubectl from PATH. build_system_prompt
    # should surface that as a RuntimeError, not propagate the dict.
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="topology fetch failed"):
        await build_system_prompt()


def test_prompt_mentions_specialised_subagents():
    prompt = build_system_prompt_from_topology("")
    # The delegation paragraph should mention each subagent by name
    assert "event_triager" in prompt
    assert "log_investigator" in prompt
    assert "metrics_analyst" in prompt


def test_prompt_explains_when_to_dispatch_subagents():
    prompt = build_system_prompt_from_topology("").lower()
    assert "delegate" in prompt or "dispatch" in prompt


def test_prompt_explains_when_NOT_to_dispatch():
    prompt = build_system_prompt_from_topology("").lower()
    # For simple direct questions, use kubectl yourself — should be in the prompt
    assert "simple" in prompt or "directly" in prompt or "yourself" in prompt
