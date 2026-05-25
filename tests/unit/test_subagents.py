"""Subagent definitions — each AgentDefinition's prompt and metadata."""

from claude_agent_sdk import AgentDefinition

from k8sense.subagents.event_triager import DEFINITION as ET_DEFINITION


def test_event_triager_is_agent_definition():
    assert isinstance(ET_DEFINITION, AgentDefinition)


def test_event_triager_description_mentions_events_and_warnings():
    desc = ET_DEFINITION.description
    assert "event" in desc.lower()
    assert "warning" in desc.lower() or "severity" in desc.lower()


def test_event_triager_prompt_includes_kubectl_get_events():
    assert "kubectl get events" in ET_DEFINITION.prompt


def test_event_triager_prompt_mentions_sort_by_timestamp():
    # The conventions block teaches the agent to sort by lastTimestamp
    assert "--sort-by" in ET_DEFINITION.prompt
    assert "lastTimestamp" in ET_DEFINITION.prompt


def test_event_triager_prompt_explains_no_warnings_case():
    # The agent should not fabricate concern when there are no warnings
    prompt = ET_DEFINITION.prompt.lower()
    assert "no warning" in prompt or "say so" in prompt or "don't fabricate" in prompt


def test_event_triager_uses_kubectl_tool_only():
    assert ET_DEFINITION.tools == ["mcp__k8sense__kubectl"]


def test_event_triager_has_reasonable_turn_budget():
    assert ET_DEFINITION.maxTurns == 8


def test_event_triager_runs_in_background_for_parallel_dispatch():
    assert ET_DEFINITION.background is True


def test_event_triager_inherits_orchestrator_model():
    assert ET_DEFINITION.model == "inherit"


from k8sense.subagents.log_investigator import DEFINITION as LI_DEFINITION  # noqa: E402


def test_log_investigator_is_agent_definition():
    assert isinstance(LI_DEFINITION, AgentDefinition)


def test_log_investigator_description_mentions_logs_and_pods():
    desc = LI_DEFINITION.description.lower()
    assert "log" in desc
    assert "pod" in desc


def test_log_investigator_prompt_starts_with_describe():
    # Convention: start with describe to read events + restart history
    assert "kubectl describe pod" in LI_DEFINITION.prompt


def test_log_investigator_prompt_uses_tail_default():
    assert "--tail=200" in LI_DEFINITION.prompt


def test_log_investigator_prompt_knows_about_previous_flag():
    assert "--previous" in LI_DEFINITION.prompt


def test_log_investigator_recognises_common_error_patterns():
    prompt = LI_DEFINITION.prompt
    assert "OOMKilled" in prompt
    assert "ImagePullBackOff" in prompt
    assert "CrashLoopBackOff" in prompt


def test_log_investigator_quotes_concrete_log_lines():
    prompt = LI_DEFINITION.prompt.lower()
    assert "quote" in prompt or "concrete" in prompt


def test_log_investigator_uses_kubectl_only():
    assert LI_DEFINITION.tools == ["mcp__k8sense__kubectl"]


def test_log_investigator_has_turn_budget_and_parallel_settings():
    assert LI_DEFINITION.maxTurns == 8
    assert LI_DEFINITION.background is True
    assert LI_DEFINITION.model == "inherit"
