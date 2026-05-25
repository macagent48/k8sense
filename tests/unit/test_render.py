"""Renderer formatting: thinking, tool_call, final, error."""

import pytest
from rich.console import Console

from k8sense.render import Renderer


@pytest.fixture
def captured():
    console = Console(record=True, force_terminal=False, width=120)
    return Renderer(console=console), console


def test_thinking_writes_to_console(captured):
    renderer, console = captured
    renderer.thinking("considering the next step")
    output = console.export_text()
    assert "considering the next step" in output


def test_tool_call_includes_command_and_args(captured):
    renderer, console = captured
    renderer.tool_call("kubectl", {"args": ["get", "pods", "-n", "argocd"]})
    output = console.export_text()
    assert "kubectl" in output
    assert "get pods -n argocd" in output


def test_tool_result_includes_truncated_stdout(captured):
    renderer, console = captured
    long_stdout = "line\n" * 200
    renderer.tool_result(stdout=long_stdout, stderr="", exit_code=0)
    output = console.export_text()
    assert "exit_code=0" in output
    # Should truncate; not print 200 lines verbatim
    line_count = output.count("\n")
    assert line_count < 60, f"expected truncated output, got {line_count} lines"


def test_final_prints_answer(captured):
    renderer, console = captured
    renderer.final("The cluster has 3 nodes and 12 namespaces.")
    output = console.export_text()
    assert "3 nodes" in output
    assert "12 namespaces" in output


def test_error_prints_message(captured):
    renderer, console = captured
    renderer.error("max tool calls exceeded")
    output = console.export_text()
    assert "max tool calls exceeded" in output


def test_tool_result_non_zero_exit_uses_yellow_border(captured):
    renderer, console = captured
    renderer.tool_result(stdout="", stderr="permission denied", exit_code=1)
    # We can't assert on ANSI styles via export_text(), but we can assert the
    # exit code and stderr show up in the panel body.
    output = console.export_text()
    assert "exit_code=1" in output
    assert "permission denied" in output


def test_tool_call_falls_back_to_repr_for_unknown_tool(captured):
    renderer, console = captured
    renderer.tool_call("some_other_tool", {"foo": "bar", "n": 3})
    output = console.export_text()
    assert "some_other_tool" in output
    assert "'foo': 'bar'" in output  # part of the dict repr
