"""Tool registry — Pydantic input models and ToolSpec factory."""

import pytest
from pydantic import ValidationError

from k8sense.tools.registry import KubectlInput, PrometheusInput


def test_kubectl_input_accepts_non_empty_args():
    parsed = KubectlInput(args=["get", "pods"])
    assert parsed.args == ["get", "pods"]


def test_kubectl_input_rejects_empty_args():
    with pytest.raises(ValidationError):
        KubectlInput(args=[])


def test_kubectl_input_json_schema_includes_description():
    schema = KubectlInput.model_json_schema()
    assert schema["properties"]["args"]["type"] == "array"
    # Description text should mention something concrete the LLM can latch onto
    assert "kubectl" in schema["properties"]["args"]["description"].lower()


def test_prometheus_input_accepts_instant_query():
    parsed = PrometheusInput(query="up")
    assert parsed.query == "up"
    assert parsed.lookback is None


def test_prometheus_input_accepts_valid_lookback():
    parsed = PrometheusInput(query="up", lookback="5m")
    assert parsed.lookback == "5m"


@pytest.mark.parametrize("bad", ["5", "5xyz", "1.5h", "5min", ""])
def test_prometheus_input_rejects_invalid_lookback(bad):
    with pytest.raises(ValidationError):
        PrometheusInput(query="up", lookback=bad)


def test_prometheus_input_rejects_empty_query():
    with pytest.raises(ValidationError):
        PrometheusInput(query="")


def test_prometheus_input_json_schema_has_lookback_pattern():
    schema = PrometheusInput.model_json_schema()
    assert schema["properties"]["query"]["type"] == "string"
    lookback = schema["properties"]["lookback"]
    # Pattern propagates to JSON Schema
    assert "pattern" in str(lookback) or "\\d+" in str(lookback)


from k8sense.tools.registry import ToolSpec, all_tool_specs  # noqa: E402


def test_all_tool_specs_returns_two_tools():
    specs = all_tool_specs()
    assert len(specs) == 2
    names = {spec.name for spec in specs}
    assert names == {"kubectl", "prometheus_query"}


def test_each_spec_has_required_fields():
    for spec in all_tool_specs():
        assert isinstance(spec, ToolSpec)
        assert spec.name
        assert spec.description
        assert spec.input_model is not None
        # Handler must be an async function (i.e. callable returning coroutine)
        assert callable(spec.handler)


def test_kubectl_spec_handler_is_kubectl_handler():
    from k8sense.tools.kubectl import kubectl_handler

    specs = {spec.name: spec for spec in all_tool_specs()}
    assert specs["kubectl"].handler is kubectl_handler


def test_prometheus_spec_handler_is_prometheus_handler():
    from k8sense.tools.prometheus import prometheus_handler

    specs = {spec.name: spec for spec in all_tool_specs()}
    assert specs["prometheus_query"].handler is prometheus_handler


def test_specs_have_pydantic_input_models():
    specs = {spec.name: spec for spec in all_tool_specs()}
    from k8sense.tools.registry import KubectlInput, PrometheusInput

    assert specs["kubectl"].input_model is KubectlInput
    assert specs["prometheus_query"].input_model is PrometheusInput
