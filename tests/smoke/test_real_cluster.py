"""End-to-end smoke test: real cluster, real SDK, real model. Manual run only.

Run with:
    K8SENSE_ALLOW_API=1 pytest -m smoke -s
"""

import asyncio
import os

import pytest
from rich.console import Console

from k8sense.agent import run_ask
from k8sense.render import Renderer


@pytest.mark.smoke
def test_list_namespaces_succeeds():
    if not os.environ.get("K8SENSE_ALLOW_API"):
        pytest.skip("smoke test requires K8SENSE_ALLOW_API=1")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("smoke test requires ANTHROPIC_API_KEY")

    console = Console(record=True, force_terminal=False, width=120)
    renderer = Renderer(console=console)
    exit_code = asyncio.run(run_ask("list every namespace in the cluster", renderer))

    output = console.export_text()
    assert exit_code == 0, f"agent exited {exit_code}\n{output}"
    assert "kube-system" in output, f"expected kube-system in output:\n{output}"


@pytest.mark.smoke
def test_health_summary_dispatches_multiple_subagents():
    if not os.environ.get("K8SENSE_ALLOW_API"):
        pytest.skip("smoke test requires K8SENSE_ALLOW_API=1")
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get(
        "CLAUDE_CODE_OAUTH_TOKEN"
    ):
        # OAuth via claude CLI is the default; explicit token works too.
        # If neither, the SDK will use the local claude CLI's login (Phase 1 verified).
        pass

    console = Console(record=True, force_terminal=False, width=120)
    renderer = Renderer(console=console)
    # The question forces a multi-source investigation
    exit_code = asyncio.run(
        run_ask(
            "give a one-paragraph health summary covering events, logs of the "
            "busiest pod, and current resource usage",
            renderer,
        )
    )
    output = console.export_text()

    assert exit_code == 0, f"agent exited {exit_code}\n{output}"
    # At least two distinct subagent dispatches must have fired
    dispatched = {
        agent
        for agent in ("event_triager", "log_investigator", "metrics_analyst")
        if f"dispatching {agent}" in output
    }
    assert len(dispatched) >= 2, (
        f"expected ≥2 subagents to dispatch, got: {dispatched}\n\n{output}"
    )
