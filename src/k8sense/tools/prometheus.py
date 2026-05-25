"""Prometheus tool: HTTP client + result formatting + SDK wrapper."""

from __future__ import annotations

import os
import re

DEFAULT_PROM_URL = "http://192.168.70.174:9090"
DEFAULT_TIMEOUT_S = 10.0
MAX_RESULT_LINES = 50
MAX_RESULT_CHARS = 8000

_LOOKBACK_RE = re.compile(r"^(\d+)(s|m|h|d)$")
_LOOKBACK_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _resolve_url() -> str:
    """Return the Prometheus base URL, honouring K8SENSE_PROM_URL env override."""
    return os.environ.get("K8SENSE_PROM_URL", DEFAULT_PROM_URL)


def _parse_lookback(lookback: str) -> int:
    """Convert '5m' / '1h' / '24h' to seconds. Raises ValueError on invalid input."""
    match = _LOOKBACK_RE.match(lookback)
    if not match:
        raise ValueError(f"invalid lookback: '{lookback}'")
    value, unit = match.groups()
    return int(value) * _LOOKBACK_SECONDS[unit]


def _compute_step(lookback_seconds: int) -> int:
    """Step size in seconds — floor at 15s, ensures ≤ 60 buckets per range query."""
    return max(15, lookback_seconds // 60)


def _render_metric(metric: dict) -> str:
    """Render a metric label dict as a Prometheus-style string."""
    if not metric:
        return "{}"
    name = metric.get("__name__", "")
    other = {k: v for k, v in metric.items() if k != "__name__"}
    if not other:
        return name
    pairs = ",".join(f'{k}="{v}"' for k, v in other.items())
    return f"{name}{{{pairs}}}"


def _format_result(data: dict) -> str:
    """Format a PromQL response data block as readable text."""
    result_type = data.get("resultType", "")
    results = data.get("result", [])
    lines = [f"resultType={result_type}", f"count={len(results)}"]

    if result_type == "vector":
        for r in results:
            metric = r.get("metric", {})
            value = r.get("value", [None, None])
            lines.append(f"{_render_metric(metric)} → {value[1]} @ {value[0]}")
    elif result_type == "matrix":
        for r in results:
            metric = r.get("metric", {})
            values = r.get("values", [])
            sample = values[:5]
            lines.append(
                f"{_render_metric(metric)} → {len(values)} points, first 5: {sample}"
            )
    else:
        for r in results:
            lines.append(str(r))

    truncated = False
    if len(lines) > MAX_RESULT_LINES:
        lines = lines[:MAX_RESULT_LINES]
        truncated = True

    joined = "\n".join(lines)
    if len(joined) > MAX_RESULT_CHARS:
        joined = joined[:MAX_RESULT_CHARS]
        truncated = True
    if truncated:
        joined += "\n… (truncated)"
    return joined


import time  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402


async def run_prometheus_query(
    query: str,
    lookback: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute a PromQL instant or range query.

    Returns {"stdout": str, "stderr": str, "exit_code": int}.
    - exit_code == 0: success
    - exit_code == 1: Prometheus returned status="error" (bad PromQL)
    - exit_code == -1: connection / lookback / non-200 error before the query ran
    """
    url = _resolve_url()

    if lookback is not None:
        try:
            lb_seconds = _parse_lookback(lookback)
        except ValueError as exc:
            return {"stdout": "", "stderr": str(exc), "exit_code": -1}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if lookback is None:
                response = await client.get(
                    f"{url}/api/v1/query", params={"query": query}
                )
            else:
                end = time.time()
                start = end - lb_seconds
                step = _compute_step(lb_seconds)
                response = await client.get(
                    f"{url}/api/v1/query_range",
                    params={"query": query, "start": start, "end": end, "step": step},
                )
    except httpx.RequestError as exc:
        return {
            "stdout": "",
            "stderr": f"prometheus unreachable: {type(exc).__name__}: {exc}",
            "exit_code": -1,
        }

    if response.status_code != 200:
        return {
            "stdout": "",
            "stderr": f"prometheus HTTP {response.status_code}: {response.text[:200]}",
            "exit_code": -1,
        }

    try:
        body = response.json()
    except ValueError:
        return {
            "stdout": "",
            "stderr": f"prometheus returned non-JSON on 200: {response.text[:200]}",
            "exit_code": -1,
        }
    if body.get("status") != "success":
        err = body.get("error", "unknown error")
        return {"stdout": "", "stderr": f"promql error: {err}", "exit_code": 1}

    return {
        "stdout": _format_result(body.get("data", {})),
        "stderr": "",
        "exit_code": 0,
    }


# --- SDK wrapper ---------------------------------------------------------

from claude_agent_sdk import tool  # noqa: E402


async def prometheus_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """Plain async handler for the Prometheus SDK tool.

    Exists as a separate symbol because the SDK's `tool()` returns a non-callable
    SdkMcpTool dataclass; we keep the handler available for direct invocation in
    tests and for any future internal callers. Mirrors the kubectl_handler shape.
    """
    query = input_data.get("query") or ""
    lookback = input_data.get("lookback") or None  # treat empty string as None

    if not query:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "$ promql\n"
                        "exit_code=-1\n"
                        "--- stdout ---\n\n"
                        "--- stderr ---\n"
                        "empty query"
                    ),
                }
            ]
        }

    result = await run_prometheus_query(query, lookback=lookback)
    mode = "range" if lookback else "instant"
    header = f"$ promql {mode} {query!r}" + (
        f" lookback={lookback}" if lookback else ""
    )
    parts = [
        header,
        f"exit_code={result['exit_code']}",
        f"--- stdout ---\n{result['stdout']}",
    ]
    if result["stderr"]:
        parts.append(f"--- stderr ---\n{result['stderr']}")
    return {"content": [{"type": "text", "text": "\n".join(parts)}]}


prometheus_tool = tool(
    name="prometheus_query",
    description=(
        "Query PromQL against the homelab Prometheus instance. "
        "Pass an instant query as `query` for a current value. For trend / range "
        "queries, also pass `lookback` (e.g. '5m', '1h', '24h'). Returns metric "
        "labels and values (truncated for large result sets). Read-only — there "
        "are no mutating PromQL operations. Examples: `node_load1`, "
        '`sum(rate(container_cpu_usage_seconds_total{namespace=\\"argocd\\"}[2m])) by (pod)`.'
    ),
    input_schema={"query": str, "lookback": str},
)(prometheus_handler)
