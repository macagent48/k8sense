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
