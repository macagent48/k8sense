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


def test_build_options_allows_only_the_kubectl_tool():
    options = build_options("SYS", model_id="claude-sonnet-4-6")
    allowed = getattr(options, "allowed_tools", None)
    assert allowed is not None
    assert any("kubectl" in t for t in allowed)
    # No other tools should slip in
    assert len(allowed) == 1


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
