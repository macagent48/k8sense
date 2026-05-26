"""Verify PreToolUse hook propagates to subagents.

If a subagent (log_investigator, say) gets asked to mutate the cluster, the
same hook should intercept its kubectl call as if the main agent had made it.

Run with:
    K8SENSE_ALLOW_API=1 pytest -m smoke tests/smoke/test_subagent_hook_propagation.py -v -s
"""

import asyncio
import os

import pytest
from rich.console import Console

from k8sense.agent import run_ask
from k8sense.permissions import PermissionMode
from k8sense.render import Renderer


@pytest.mark.smoke
def test_hook_intercepts_subagent_mutation_attempt():
    if not os.environ.get("K8SENSE_ALLOW_API"):
        pytest.skip("smoke test requires K8SENSE_ALLOW_API=1")

    console = Console(record=True, force_terminal=False, width=120)
    renderer = Renderer(console=console)
    # Phrase the question to route through log_investigator, which then might
    # try to remediate. In propose mode the hook should surface ANY attempted
    # mutation regardless of which agent (main or subagent) tried to make it.
    exit_code = asyncio.run(
        run_ask(
            "investigate the argocd-server pod logs; if you see OOMKilled, "
            "propose deleting the pod to force a restart",
            renderer,
            mode=PermissionMode.PROPOSE,
        )
    )
    output = console.export_text()
    assert exit_code == 0, f"agent exited {exit_code}\n{output}"
    # Whether the main agent OR a subagent attempted the mutation, the propose
    # marker should appear. This is the load-bearing assertion.
    has_propose = "propose mode" in output.lower() or "Proposed (not executed" in output
    # If the model didn't try a mutation at all (e.g., it answered without delegating
    # or it just discussed without calling the tool), that's an LLM-behaviour
    # outcome, not a hook propagation failure. In that case we don't have signal
    # on whether the hook propagates. Mark the test xfail-like by passing.
    if "kubectl" not in output.lower():
        pytest.skip(
            "model didn't attempt any kubectl call; no signal on hook propagation"
        )
    # If the model DID call kubectl with a mutation, the marker must appear.
    if any(
        verb in output.lower() for verb in ["delete pod", "rollout restart", "cordon"]
    ):
        assert has_propose, (
            f"model attempted a mutation but propose marker missing — "
            f"hook did NOT propagate to whichever agent made the call:\n{output}"
        )
