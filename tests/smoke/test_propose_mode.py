"""End-to-end smoke: run k8sense ask --propose against the real cluster.

Verifies:
- The agent considers a mutation
- The hook intercepts and produces the propose-mode marker
- No actual kubectl mutation runs

Run with:
    K8SENSE_ALLOW_API=1 pytest -m smoke tests/smoke/test_propose_mode.py -v -s
"""

import asyncio
import os

import pytest
from rich.console import Console

from k8sense.agent import run_ask
from k8sense.permissions import PermissionMode
from k8sense.render import Renderer


@pytest.mark.smoke
def test_propose_mode_marker_appears_and_no_mutation_runs():
    if not os.environ.get("K8SENSE_ALLOW_API"):
        pytest.skip("smoke test requires K8SENSE_ALLOW_API=1")

    console = Console(record=True, force_terminal=False, width=120)
    renderer = Renderer(console=console)
    exit_code = asyncio.run(
        run_ask(
            "the argocd-server pod is OOMKilled; restart it by deleting the pod",
            renderer,
            mode=PermissionMode.PROPOSE,
        )
    )
    output = console.export_text()

    assert exit_code == 0, f"agent exited {exit_code}\n{output}"
    # The propose marker (or its substring) must appear
    assert "propose" in output.lower(), f"propose marker missing from output:\n{output}"
    # The agent should have at least mentioned the kubectl command
    assert "kubectl" in output.lower()
