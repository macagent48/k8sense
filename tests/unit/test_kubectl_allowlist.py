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


def test_allowed_verbs_are_case_sensitive():
    # Verbs are stored lowercase; uppercase is silently rejected. Pinned so
    # any future change to case-insensitive matching breaks a test loudly.
    assert is_allowed(["GET"]) is False
    assert is_allowed(["Get"]) is False


def test_flag_as_first_arg_is_rejected():
    # Flags are not verbs. Catches naive callers who omit the verb.
    assert is_allowed(["--all-namespaces"]) is False
    assert is_allowed(["--help"]) is False


def test_space_separated_verb_string_is_rejected():
    # A whole command shoved into a single string element is not a verb.
    assert is_allowed(["get pods"]) is False
