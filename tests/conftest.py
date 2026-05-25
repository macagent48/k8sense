"""Shared test fixtures."""

import os
import pytest


@pytest.fixture(autouse=True)
def _no_real_api_calls(monkeypatch):
    """Guard unit tests from accidentally hitting the live API.

    Integration tests explicitly opt in by setting K8SENSE_ALLOW_API=1 in
    their own setup. Unit tests must never touch the network.
    """
    if not os.environ.get("K8SENSE_ALLOW_API"):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
