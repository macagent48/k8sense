"""Pure helpers in agent.py: options builder, tool-call counter, message dispatch."""

from k8sense.agent import (
    MAX_TOOL_CALLS,
    ToolBudget,
    build_options,
    parse_exit_code,
)


def test_build_options_returns_object_with_system_prompt():
    options = build_options("SYS PROMPT", model_id="claude-sonnet-4-6")
    assert getattr(options, "system_prompt", None) == "SYS PROMPT"


def test_build_options_includes_kubectl_and_prometheus_tools():
    options = build_options("SYS", model_id="claude-sonnet-4-6")
    allowed = getattr(options, "allowed_tools", None)
    assert allowed is not None
    assert any("kubectl" in t for t in allowed)
    assert any("prometheus" in t for t in allowed)
    # Exactly these two in Phase 2
    assert len(allowed) == 2


def test_tool_budget_allows_calls_under_limit():
    budget = ToolBudget(limit=3)
    assert budget.charge() is True
    assert budget.charge() is True
    assert budget.charge() is True


def test_tool_budget_rejects_after_limit():
    budget = ToolBudget(limit=2)
    budget.charge()
    budget.charge()
    assert budget.charge() is False


def test_default_tool_budget_matches_max_tool_calls():
    budget = ToolBudget()
    assert budget.limit == MAX_TOOL_CALLS


def test_parse_exit_code_extracts_from_handler_output():
    # The kubectl_handler returns text in the form "$ kubectl X\nexit_code=N\n..."
    text = "$ kubectl get pods\nexit_code=0\n--- stdout ---\nNAME   READY\n"
    assert parse_exit_code(text) == 0


def test_parse_exit_code_extracts_non_zero():
    text = "$ kubectl get pods\nexit_code=-1\n--- stderr ---\ntimeout\n"
    assert parse_exit_code(text) == -1


def test_parse_exit_code_returns_zero_when_missing():
    # If the text doesn't contain exit_code=, assume 0 (don't crash)
    assert parse_exit_code("some unrelated output") == 0


from k8sense.agent import parse_handler_envelope  # noqa: E402


def test_parse_envelope_extracts_stdout_and_stderr():
    text = "$ kubectl get pods\nexit_code=0\n--- stdout ---\nNAME   READY\npod-1  1/1\n"
    exit_code, stdout, stderr = parse_handler_envelope(text)
    assert exit_code == 0
    assert "NAME   READY" in stdout
    assert "pod-1  1/1" in stdout
    assert stderr == ""


def test_parse_envelope_handles_stderr_section():
    text = (
        "$ kubectl get pods -n nope\n"
        "exit_code=1\n"
        "--- stdout ---\n"
        "\n"
        "--- stderr ---\n"
        "Error: namespace 'nope' not found\n"
    )
    exit_code, stdout, stderr = parse_handler_envelope(text)
    assert exit_code == 1
    assert "namespace 'nope' not found" in stderr


def test_parse_envelope_falls_back_for_unrecognised_text():
    text = "some unrelated output\nwith multiple lines"
    exit_code, stdout, stderr = parse_handler_envelope(text)
    assert exit_code == 0
    assert "some unrelated output" in stdout
    assert stderr == ""


def test_build_options_includes_three_subagents():
    options = build_options("SYS", model_id="claude-sonnet-4-6")
    agents = getattr(options, "agents", None)
    assert agents is not None
    assert set(agents.keys()) == {
        "event_triager",
        "log_investigator",
        "metrics_analyst",
    }
    # Each should be an AgentDefinition
    from claude_agent_sdk import AgentDefinition

    for name, agent in agents.items():
        assert isinstance(agent, AgentDefinition), f"{name} is not AgentDefinition"


from k8sense.agent import (  # noqa: E402
    SUBAGENT_DISPATCH_TOOL_NAME,
    is_subagent_dispatch,
    extract_subagent_dispatch,
)


def test_is_subagent_dispatch_detects_task_tool():
    assert is_subagent_dispatch(SUBAGENT_DISPATCH_TOOL_NAME) is True


def test_is_subagent_dispatch_rejects_kubectl():
    assert is_subagent_dispatch("mcp__k8sense__kubectl") is False


def test_extract_subagent_dispatch_pulls_name_and_brief():
    block_input = {
        "subagent_type": "log_investigator",
        "description": "investigate argocd-server",
    }
    name, brief = extract_subagent_dispatch(block_input)
    assert name == "log_investigator"
    assert brief == "investigate argocd-server"


def test_extract_subagent_dispatch_uses_prompt_if_no_description():
    block_input = {
        "subagent_type": "event_triager",
        "prompt": "scan warnings in argocd",
    }
    name, brief = extract_subagent_dispatch(block_input)
    assert name == "event_triager"
    assert "scan warnings" in brief


def test_extract_subagent_dispatch_truncates_long_brief():
    long = "x" * 500
    block_input = {"subagent_type": "et", "description": long}
    _, brief = extract_subagent_dispatch(block_input)
    assert len(brief) <= 120


def test_build_options_accepts_mode_and_wires_hook():
    from k8sense.permissions import PermissionMode

    options = build_options(
        "SYS", model_id="claude-sonnet-4-6", mode=PermissionMode.AUTO_SAFE
    )
    hooks = getattr(options, "hooks", None)
    assert hooks is not None
    assert "PreToolUse" in hooks
    matchers = hooks["PreToolUse"]
    assert len(matchers) >= 1
    # The matcher should target the kubectl tool
    matcher = matchers[0]
    matcher_attr = (
        matcher.get("matcher")
        if isinstance(matcher, dict)
        else getattr(matcher, "matcher", None)
    )
    assert matcher_attr == "mcp__k8sense__kubectl"


def test_build_options_default_mode_is_readonly():
    options = build_options("SYS", model_id="claude-sonnet-4-6")
    # Hook is still attached
    hooks = getattr(options, "hooks", None)
    assert hooks is not None
    assert "PreToolUse" in hooks
