"""Fingerprint scoring for eval results."""

from evals.runner import EvalCase, EvalResult, score_fingerprints


def _result(final_text: str = "", tool_calls: list[dict] | None = None) -> EvalResult:
    return EvalResult(final_text=final_text, tool_calls=tool_calls or [])


def test_substring_match_passes():
    case = EvalCase(
        id="t1",
        question="?",
        fingerprints=[
            {"type": "substring", "value": "argocd"},
        ],
    )
    result = _result(final_text="argocd is healthy")
    passes, failures = score_fingerprints(case, result)
    assert passes is True
    assert failures == []


def test_substring_match_fails_when_missing():
    case = EvalCase(
        id="t2",
        question="?",
        fingerprints=[
            {"type": "substring", "value": "argocd"},
        ],
    )
    result = _result(final_text="no relevant content")
    passes, failures = score_fingerprints(case, result)
    assert passes is False
    assert "substring 'argocd'" in failures[0]


def test_regex_match_passes():
    case = EvalCase(
        id="t3",
        question="?",
        fingerprints=[
            {"type": "regex", "value": r"v\d+\.\d+\.\d+"},
        ],
    )
    result = _result(final_text="cluster version v1.29.3 detected")
    passes, _ = score_fingerprints(case, result)
    assert passes is True


def test_structural_tool_name_passes():
    case = EvalCase(
        id="t4",
        question="?",
        fingerprints=[
            {"type": "tool_called", "value": "kubectl"},
        ],
    )
    result = _result(
        tool_calls=[{"name": "kubectl", "input": {"args": ["get", "pods"]}}]
    )
    passes, _ = score_fingerprints(case, result)
    assert passes is True


def test_structural_tool_call_args_contains():
    case = EvalCase(
        id="t5",
        question="?",
        fingerprints=[
            {"type": "tool_args_contains", "value": "argocd"},
        ],
    )
    result = _result(
        tool_calls=[
            {"name": "kubectl", "input": {"args": ["get", "pods", "-n", "argocd"]}}
        ]
    )
    passes, _ = score_fingerprints(case, result)
    assert passes is True


def test_all_fingerprints_required():
    case = EvalCase(
        id="t6",
        question="?",
        fingerprints=[
            {"type": "substring", "value": "argocd"},
            {"type": "substring", "value": "longhorn"},
        ],
    )
    result = _result(final_text="argocd is healthy")  # missing longhorn
    passes, failures = score_fingerprints(case, result)
    assert passes is False
    assert len(failures) == 1
    assert "longhorn" in failures[0]


def test_unknown_fingerprint_type_produces_failure_message():
    case = EvalCase(
        id="t7",
        question="?",
        fingerprints=[
            {"type": "webhook", "value": "anything"},
        ],
    )
    result = _result(final_text="some content")
    passes, failures = score_fingerprints(case, result)
    assert passes is False
    assert "unknown fingerprint type: webhook" in failures[0]


def test_unknown_fingerprint_type_without_value_does_not_crash():
    case = EvalCase(
        id="t8",
        question="?",
        fingerprints=[
            {"type": "webhook"},  # missing 'value' key
        ],
    )
    result = _result(final_text="some content")
    # Must not raise; must produce a failure
    passes, failures = score_fingerprints(case, result)
    assert passes is False
    assert "unknown fingerprint type" in failures[0]


def test_malformed_regex_produces_failure_message_not_crash():
    case = EvalCase(
        id="t9",
        question="?",
        fingerprints=[
            {"type": "regex", "value": "[unclosed"},
        ],
    )
    result = _result(final_text="anything")
    passes, failures = score_fingerprints(case, result)
    assert passes is False
    assert "regex '[unclosed' is invalid" in failures[0]


def test_regex_failure_when_pattern_not_found():
    case = EvalCase(
        id="t10",
        question="?",
        fingerprints=[
            {"type": "regex", "value": r"\d+ pods?"},
        ],
    )
    result = _result(final_text="no numbers here")
    passes, failures = score_fingerprints(case, result)
    assert passes is False
    assert "did not match" in failures[0]


def test_tool_called_failure_when_tool_never_invoked():
    case = EvalCase(
        id="t11",
        question="?",
        fingerprints=[
            {"type": "tool_called", "value": "kubectl"},
        ],
    )
    result = _result(tool_calls=[])
    passes, failures = score_fingerprints(case, result)
    assert passes is False
    assert "tool 'kubectl' was never called" in failures[0]


def test_empty_fingerprints_list_passes_trivially():
    case = EvalCase(id="t12", question="?", fingerprints=[])
    result = _result(final_text="anything")
    passes, failures = score_fingerprints(case, result)
    assert passes is True
    assert failures == []


def test_tool_called_matches_mcp_prefixed_name():
    """SDK emits 'mcp__<server>__<tool>'; a dataset fingerprint of just 'kubectl' must match."""
    case = EvalCase(
        id="t13",
        question="?",
        fingerprints=[
            {"type": "tool_called", "value": "kubectl"},
        ],
    )
    result = _result(
        tool_calls=[
            {"name": "mcp__k8sense__kubectl", "input": {"args": ["get", "pods"]}},
        ]
    )
    passes, _ = score_fingerprints(case, result)
    assert passes is True
