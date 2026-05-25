"""Allowlist rules for the kubectl tool."""

import pytest

from k8sense.tools.kubectl import is_allowed


@pytest.mark.parametrize(
    "verb", ["get", "describe", "logs", "top", "events", "version"]
)
def test_allowed_verbs_pass(verb):
    assert is_allowed([verb, "pods"]) is True


@pytest.mark.parametrize(
    "verb", ["delete", "apply", "create", "scale", "patch", "edit", "exec", "rollout"]
)
def test_mutating_verbs_rejected(verb):
    assert is_allowed([verb, "pod", "x"]) is False


def test_empty_args_rejected():
    assert is_allowed([]) is False


def test_unknown_verb_rejected():
    assert is_allowed(["frobnicate"]) is False
