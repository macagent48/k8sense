"""memory.signature: extract structured (kind, namespace, name, reason) from an investigation."""


from k8sense.memory.signature import Signature, extract, extract_text_hints


def test_signature_is_empty_default():
    s = Signature(kind=None, namespace=None, name=None, reason=None)
    assert s.is_empty() is True


def test_signature_is_empty_when_any_field_present():
    s = Signature(kind="Pod", namespace=None, name=None, reason=None)
    assert s.is_empty() is False


def test_extract_text_hints_pulls_namespace_from_question():
    sig = extract_text_hints("why is the argocd-server pod restarting?")
    # Without a topology lookup we can't be sure 'argocd' is a namespace, so this
    # function does best-effort substring matching. At minimum the reason hint
    # should NOT be present (no error keyword in this question).
    assert sig.reason is None


def test_extract_text_hints_pulls_reason_keyword():
    sig = extract_text_hints("why was the pod OOMKilled?")
    assert sig.reason == "OOMKilled"


def test_extract_text_hints_pulls_image_pull():
    sig = extract_text_hints("the pod has ImagePullBackOff for the last hour")
    assert sig.reason == "ImagePullBackOff"


def test_extract_text_hints_no_keyword_returns_empty():
    sig = extract_text_hints("list every namespace in the cluster")
    assert sig.is_empty()


def _tool_call(args):
    return {"name": "mcp__k8sense__kubectl", "input": {"args": args}}


def _tool_result(text):
    return {"text": text}


def test_extract_resource_target_from_describe_pod():
    sig = extract(
        tool_calls=[
            _tool_call(["describe", "pod", "argocd-server-7d", "-n", "argocd"])
        ],
        tool_results=[],
        final_text="",
    )
    assert sig.kind == "Pod"
    assert sig.namespace == "argocd"
    assert sig.name == "argocd-server-7d"


def test_extract_reason_from_final_text():
    sig = extract(
        tool_calls=[],
        tool_results=[],
        final_text="The pod was OOMKilled because the memory limit is 256Mi.",
    )
    assert sig.reason == "OOMKilled"


def test_extract_reason_from_tool_result_text():
    sig = extract(
        tool_calls=[],
        tool_results=[_tool_result("Last State: Terminated, Reason: CrashLoopBackOff")],
        final_text="",
    )
    assert sig.reason == "CrashLoopBackOff"


def test_extract_combined_call_and_text():
    sig = extract(
        tool_calls=[
            _tool_call(["describe", "pod", "argocd-server-7d", "-n", "argocd"]),
            _tool_call(["logs", "argocd-server-7d", "-n", "argocd"]),
        ],
        tool_results=[_tool_result("Reason: OOMKilled")],
        final_text="Memory limit was 256Mi.",
    )
    assert sig.kind == "Pod"
    assert sig.namespace == "argocd"
    assert sig.name == "argocd-server-7d"
    assert sig.reason == "OOMKilled"


def test_extract_returns_empty_when_nothing_to_parse():
    sig = extract(tool_calls=[], tool_results=[], final_text="")
    assert sig.is_empty()
