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
