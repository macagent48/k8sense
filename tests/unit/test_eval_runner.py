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
