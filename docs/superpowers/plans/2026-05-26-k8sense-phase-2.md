# k8sense Phase 2 Implementation Plan (Subagents)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three specialised subagents (`event_triager`, `log_investigator`, `metrics_analyst`) and one new tool (`prometheus_query`) to the existing k8sense CLI, wired through `ClaudeAgentOptions.agents={}` with `AgentDefinition.background=True` for parallel dispatch.

**Architecture:** Each subagent is an `AgentDefinition` exported from its own module under `src/k8sense/subagents/`. The orchestrator agent (Phase 1) gains a system-prompt addendum about delegation and is wired with the three subagent definitions. A new `prometheus_query` tool joins kubectl in the existing MCP server; metrics_analyst is the only subagent allowed to use it. Eval harness gains a `subagent_called` fingerprint type and 5 new multi-source questions.

**Tech Stack:**

- Existing Phase 1 stack (`claude-agent-sdk` 0.2.87, `rich`, `pytest`, `python-dotenv`)
- New: `httpx` for async Prometheus HTTP
- External: Prometheus at `http://192.168.70.174:9090` (overridable via `K8SENSE_PROM_URL`)

**Spec:** `docs/superpowers/specs/2026-05-26-k8sense-phase-2-design.md`
**Predecessor:** Phase 1 at git tag `phase-1`, current `HEAD` at `f9a2b17` (spec commit)

**Testing discipline:** Inherits Phase 1's "strict TDD for deterministic code, eval harness for LLM behaviour." No mocking of Prometheus, kubectl, or the SDK. Connection-refused tests use a deliberately-invalid `K8SENSE_PROM_URL` (env manipulation, not mocking).

---

## File Structure

```
src/k8sense/
├── agent.py                     # MODIFY: wire agents={} + register prometheus_tool
├── prompts/system.py            # MODIFY: append delegation paragraph
├── render.py                    # MODIFY: add subagent_dispatch() method
├── tools/
│   ├── kubectl.py               # unchanged
│   └── prometheus.py            # NEW (~120 lines incl. SDK wrapper)
└── subagents/                   # NEW directory
    ├── __init__.py              # re-exports the 3 DEFINITIONs
    ├── event_triager.py         # ~40 lines
    ├── log_investigator.py      # ~50 lines
    └── metrics_analyst.py       # ~60 lines

evals/
├── dataset.jsonl                # MODIFY: +5 entries (10 → 15)
└── runner.py                    # MODIFY: +subagent_called fingerprint

tests/
├── unit/
│   ├── test_prometheus_tool.py  # NEW
│   ├── test_subagents.py        # NEW
│   ├── test_agent_helpers.py    # MODIFY: +agents={} wiring test
│   ├── test_render.py           # MODIFY: +subagent_dispatch test
│   ├── test_eval_runner.py      # MODIFY: +subagent_called tests
│   └── test_system_prompt.py    # MODIFY: +delegation paragraph test
└── smoke/
    └── test_real_cluster.py     # MODIFY: +multi-source dispatch smoke

pyproject.toml                   # MODIFY: +httpx dep
README.md                        # MODIFY: phase 2 status note
```

**Responsibilities:**

- `tools/prometheus.py` — HTTP client (httpx), URL resolution, lookback parser, response formatting, error envelope, `@tool` SDK wrapper. Mirrors the structure of `tools/kubectl.py`.
- `subagents/<name>.py` — exports `DEFINITION: AgentDefinition`. Pure data + a prompt string. Tested by asserting key conventions appear in the prompt.
- `agent.py` build_options changes — register `prometheus_tool` in the MCP server's `tools=[...]`, add `"mcp__k8sense__prometheus_query"` to `allowed_tools`, pass `agents={}` dict.
- `prompts/system.py` — one new paragraph in the template.
- `render.py` — one new method, follows the existing style of the others.
- `evals/runner.py` — one new branch in `score_fingerprints`.
- `evals/dataset.jsonl` — five new lines.

---

## Task 1: Add httpx dependency

**Files:**

- Modify: `pyproject.toml`

- [ ] **Step 1: Add `httpx` to dependencies**

Edit `pyproject.toml`. Find the `dependencies` list:

```toml
dependencies = [
    "claude-agent-sdk>=0.1.0",
    "rich>=13.7",
    "python-dotenv>=1.0",
]
```

Change to:

```toml
dependencies = [
    "claude-agent-sdk>=0.1.0",
    "rich>=13.7",
    "python-dotenv>=1.0",
    "httpx>=0.27",
]
```

- [ ] **Step 2: Install the new dependency**

Run:

```bash
.venv/bin/pip install -e ".[dev]"
```

Expected: pip installs httpx (and its h11, anyio, sniffio, certifi, idna deps). Exit 0.

- [ ] **Step 3: Verify httpx imports**

Run:

```bash
.venv/bin/python -c "import httpx; print('httpx', httpx.__version__)"
```

Expected: prints a version ≥ 0.27.x.

- [ ] **Step 4: Confirm prior tests still pass**

Run:

```bash
.venv/bin/pytest -v
```

Expected: 72 passed, 1 skipped (unchanged from Phase 1).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "Add httpx dependency for Prometheus tool"
```

---

## Task 2: Prometheus URL resolver + lookback parser (pure logic — TDD)

**Files:**

- Create: `tests/unit/test_prometheus_tool.py`
- Create: `src/k8sense/tools/prometheus.py`

- [ ] **Step 1: Write the failing test for URL resolution and lookback parsing**

Create `tests/unit/test_prometheus_tool.py`:

```python
"""Prometheus tool: URL resolution, lookback parsing, step computation."""
import pytest

from k8sense.tools.prometheus import (
    DEFAULT_PROM_URL,
    _compute_step,
    _parse_lookback,
    _resolve_url,
)


def test_default_prom_url_is_homelab_address():
    assert DEFAULT_PROM_URL == "http://192.168.70.174:9090"


def test_resolve_url_returns_default_without_env(monkeypatch):
    monkeypatch.delenv("K8SENSE_PROM_URL", raising=False)
    assert _resolve_url() == DEFAULT_PROM_URL


def test_resolve_url_honours_env_override(monkeypatch):
    monkeypatch.setenv("K8SENSE_PROM_URL", "http://prom.example.com:9090")
    assert _resolve_url() == "http://prom.example.com:9090"


@pytest.mark.parametrize("lookback,expected_seconds", [
    ("30s", 30),
    ("5m", 300),
    ("1h", 3600),
    ("24h", 86400),
    ("2d", 172800),
])
def test_parse_lookback_valid(lookback, expected_seconds):
    assert _parse_lookback(lookback) == expected_seconds


@pytest.mark.parametrize("bad", ["5", "5x", "abc", "", "5min", "h1"])
def test_parse_lookback_rejects_invalid(bad):
    with pytest.raises(ValueError, match="invalid lookback"):
        _parse_lookback(bad)


def test_compute_step_caps_at_60_buckets():
    # 1h = 3600s ⇒ step ≥ 60s (so ≤ 60 buckets)
    assert _compute_step(3600) == 60


def test_compute_step_floor_is_15s():
    # 1m of data shouldn't produce a 1s step
    assert _compute_step(60) == 15
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_prometheus_tool.py -v
```

Expected: `ImportError: No module named 'k8sense.tools.prometheus'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/k8sense/tools/prometheus.py`:

```python
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
```

- [ ] **Step 4: Run the test — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_prometheus_tool.py -v
```

Expected: All 12 tests pass (3 URL + 5 lookback + 2 rejection + 2 step).

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_prometheus_tool.py src/k8sense/tools/prometheus.py
git commit -m "Add Prometheus URL resolver and lookback parser"
```

---

## Task 3: PromQL result formatter (pure logic — TDD)

**Files:**

- Modify: `src/k8sense/tools/prometheus.py`
- Modify: `tests/unit/test_prometheus_tool.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_prometheus_tool.py`:

```python


from k8sense.tools.prometheus import _format_result, _render_metric  # noqa: E402


def test_render_metric_includes_labels():
    rendered = _render_metric({"__name__": "node_load1", "instance": "master:9100"})
    assert "node_load1" in rendered
    assert 'instance="master:9100"' in rendered


def test_render_metric_handles_empty():
    assert _render_metric({}) == "{}"


def test_format_vector_result_includes_value_and_timestamp():
    data = {
        "resultType": "vector",
        "result": [
            {"metric": {"__name__": "node_load1", "instance": "master"},
             "value": [1234567890, "0.42"]},
        ],
    }
    output = _format_result(data)
    assert "resultType=vector" in output
    assert "count=1" in output
    assert "node_load1" in output
    assert "0.42" in output


def test_format_matrix_result_summarises_points():
    data = {
        "resultType": "matrix",
        "result": [
            {"metric": {"__name__": "node_load1"},
             "values": [[1, "0.1"], [2, "0.2"], [3, "0.3"], [4, "0.4"], [5, "0.5"], [6, "0.6"]]},
        ],
    }
    output = _format_result(data)
    assert "resultType=matrix" in output
    assert "6 points" in output  # total
    # first 5 should appear, not all 6
    assert "[6, '0.6']" not in output or "first 5" in output


def test_format_result_truncates_when_too_many_lines():
    data = {
        "resultType": "vector",
        "result": [
            {"metric": {"__name__": "x", "i": str(i)}, "value": [0, "1"]}
            for i in range(100)
        ],
    }
    output = _format_result(data)
    assert "truncated" in output
    assert output.count("\n") < 55  # well under MAX_RESULT_LINES
```

- [ ] **Step 2: Run the tests — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_prometheus_tool.py -v
```

Expected: 5 failures with `ImportError: cannot import name '_format_result' from 'k8sense.tools.prometheus'`.

- [ ] **Step 3: Add the formatters to `src/k8sense/tools/prometheus.py`**

Append below `_compute_step`:

```python


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
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_prometheus_tool.py -v
```

Expected: All 17 tests pass (12 prior + 5 new).

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_prometheus_tool.py src/k8sense/tools/prometheus.py
git commit -m "Add PromQL result formatter with truncation"
```

---

## Task 4: Prometheus HTTP query (instant + range) — TDD

**Files:**

- Modify: `src/k8sense/tools/prometheus.py`
- Modify: `tests/unit/test_prometheus_tool.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_prometheus_tool.py`:

```python


import os  # noqa: E402

from k8sense.tools.prometheus import run_prometheus_query  # noqa: E402


@pytest.mark.asyncio
async def test_run_query_returns_error_when_prometheus_unreachable(monkeypatch):
    # Point at a deliberately-invalid address — no mocking, just env redirection.
    monkeypatch.setenv("K8SENSE_PROM_URL", "http://127.0.0.1:1")
    result = await run_prometheus_query("up")
    assert result["exit_code"] == -1
    assert "unreachable" in result["stderr"].lower() or "connect" in result["stderr"].lower()


@pytest.mark.asyncio
async def test_run_query_returns_error_for_invalid_lookback():
    result = await run_prometheus_query("up", lookback="5xyz")
    assert result["exit_code"] == -1
    assert "invalid lookback" in result["stderr"]


_REAL_PROM = os.environ.get("K8SENSE_PROM_URL_FOR_TESTS", "http://192.168.70.174:9090")


def _prom_reachable() -> bool:
    """Quick TCP probe — used to skip live tests when Prom is down."""
    import socket
    from urllib.parse import urlparse
    p = urlparse(_REAL_PROM)
    try:
        with socket.create_connection((p.hostname, p.port or 9090), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _prom_reachable(), reason="Prometheus not reachable")
@pytest.mark.asyncio
async def test_instant_query_against_real_prometheus(monkeypatch):
    monkeypatch.setenv("K8SENSE_PROM_URL", _REAL_PROM)
    # 'up' is the simplest universally-available metric
    result = await run_prometheus_query("up")
    assert result["exit_code"] == 0, result["stderr"]
    assert "resultType=vector" in result["stdout"]


@pytest.mark.skipif(not _prom_reachable(), reason="Prometheus not reachable")
@pytest.mark.asyncio
async def test_range_query_against_real_prometheus(monkeypatch):
    monkeypatch.setenv("K8SENSE_PROM_URL", _REAL_PROM)
    result = await run_prometheus_query("up", lookback="5m")
    assert result["exit_code"] == 0, result["stderr"]
    assert "resultType=matrix" in result["stdout"]
```

- [ ] **Step 2: Run the tests — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_prometheus_tool.py::test_run_query_returns_error_when_prometheus_unreachable -v
```

Expected: `ImportError: cannot import name 'run_prometheus_query' from 'k8sense.tools.prometheus'`.

- [ ] **Step 3: Implement `run_prometheus_query`**

Append to `src/k8sense/tools/prometheus.py`:

```python


import time
from typing import Any

import httpx


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

    body = response.json()
    if body.get("status") != "success":
        err = body.get("error", "unknown error")
        return {"stdout": "", "stderr": f"promql error: {err}", "exit_code": 1}

    return {
        "stdout": _format_result(body.get("data", {})),
        "stderr": "",
        "exit_code": 0,
    }
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_prometheus_tool.py -v
```

Expected: 21 tests pass (17 prior + 4 new). If Prometheus is reachable, the two live tests run and pass; otherwise they skip.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_prometheus_tool.py src/k8sense/tools/prometheus.py
git commit -m "Add async Prometheus query (instant + range with lookback)"
```

---

## Task 5: Prometheus @tool SDK wrapper

**Files:**

- Modify: `src/k8sense/tools/prometheus.py`
- Modify: `tests/unit/test_prometheus_tool.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_prometheus_tool.py`:

```python


from k8sense.tools.prometheus import prometheus_handler, prometheus_tool  # noqa: E402


@pytest.mark.asyncio
async def test_handler_returns_envelope_format_for_unreachable(monkeypatch):
    monkeypatch.setenv("K8SENSE_PROM_URL", "http://127.0.0.1:1")
    result = await prometheus_handler({"query": "up"})
    assert "content" in result
    text = result["content"][0]["text"]
    assert text.startswith("$ promql ")
    assert "exit_code=-1" in text
    assert "--- stdout ---" in text
    assert "--- stderr ---" in text


@pytest.mark.asyncio
async def test_handler_rejects_empty_query():
    result = await prometheus_handler({"query": ""})
    text = result["content"][0]["text"]
    assert "exit_code=-1" in text
    assert "empty query" in text


def test_prometheus_tool_is_sdkmcptool():
    # Same two-name pattern as kubectl_handler / kubectl_tool
    from claude_agent_sdk import SdkMcpTool
    assert isinstance(prometheus_tool, SdkMcpTool)
    assert prometheus_tool.handler is prometheus_handler
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_prometheus_tool.py -v
```

Expected: 3 failures with `ImportError: cannot import name 'prometheus_handler' from 'k8sense.tools.prometheus'`.

- [ ] **Step 3: Append the SDK wrapper to `src/k8sense/tools/prometheus.py`**

Append at the bottom:

```python


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
            "content": [{
                "type": "text",
                "text": (
                    "$ promql\n"
                    "exit_code=-1\n"
                    "--- stdout ---\n\n"
                    "--- stderr ---\n"
                    "empty query"
                ),
            }]
        }

    result = await run_prometheus_query(query, lookback=lookback)
    mode = "range" if lookback else "instant"
    header = f"$ promql {mode} {query!r}" + (f" lookback={lookback}" if lookback else "")
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
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_prometheus_tool.py -v
```

Expected: 24 tests pass (21 prior + 3 new).

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: 96 passed, 1 skipped (72 from Phase 1 + 24 new Prometheus tests).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_prometheus_tool.py src/k8sense/tools/prometheus.py
git commit -m "Add @tool-decorated Prometheus wrapper for the SDK"
```

---

## Task 6: Register prometheus_tool in agent.build_options

**Files:**

- Modify: `src/k8sense/agent.py`
- Modify: `tests/unit/test_agent_helpers.py`

- [ ] **Step 1: Update the failing test for `build_options`**

The existing `test_build_options_allows_only_the_kubectl_tool` will need updating. Edit `tests/unit/test_agent_helpers.py`. Find and replace:

```python
def test_build_options_allows_only_the_kubectl_tool():
    options = build_options("SYS", model_id="claude-sonnet-4-6")
    allowed = getattr(options, "allowed_tools", None)
    assert allowed is not None
    assert any("kubectl" in t for t in allowed)
    # No other tools should slip in
    assert len(allowed) == 1
```

With:

```python
def test_build_options_includes_kubectl_and_prometheus_tools():
    options = build_options("SYS", model_id="claude-sonnet-4-6")
    allowed = getattr(options, "allowed_tools", None)
    assert allowed is not None
    assert any("kubectl" in t for t in allowed)
    assert any("prometheus" in t for t in allowed)
    # Exactly these two in Phase 2
    assert len(allowed) == 2
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
.venv/bin/pytest tests/unit/test_agent_helpers.py::test_build_options_includes_kubectl_and_prometheus_tools -v
```

Expected: FAIL — `allowed` currently has only the kubectl tool.

- [ ] **Step 3: Update `build_options` in `src/k8sense/agent.py`**

Find the existing `build_options` function. The `create_sdk_mcp_server` call and `allowed_tools` list both need updating. Replace:

```python
def build_options(system_prompt: str, model_id: str = DEFAULT_MODEL) -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions wired with the kubectl tool only."""
    server = create_sdk_mcp_server(
        name="k8sense",
        version="0.1.0",
        tools=[kubectl_tool],
    )
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"k8sense": server},
        allowed_tools=["mcp__k8sense__kubectl"],
        model=model_id,
    )
```

With:

```python
def build_options(system_prompt: str, model_id: str = DEFAULT_MODEL) -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions wired with kubectl + prometheus tools."""
    server = create_sdk_mcp_server(
        name="k8sense",
        version="0.2.0",
        tools=[kubectl_tool, prometheus_tool],
    )
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"k8sense": server},
        allowed_tools=[
            "mcp__k8sense__kubectl",
            "mcp__k8sense__prometheus_query",
        ],
        model=model_id,
    )
```

And add `prometheus_tool` to the imports at the top of `agent.py`. Find:

```python
from k8sense.tools.kubectl import kubectl_tool
```

Change to:

```python
from k8sense.tools.kubectl import kubectl_tool
from k8sense.tools.prometheus import prometheus_tool
```

- [ ] **Step 4: Run the updated test — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_agent_helpers.py -v
```

Expected: All tests pass (the previous `test_build_options_returns_object_with_system_prompt` still holds; the renamed allowlist test now passes).

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: 96 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/agent.py tests/unit/test_agent_helpers.py
git commit -m "Register prometheus_tool alongside kubectl in build_options"
```

---

## Task 7: event_triager subagent (TDD by prompt assertions)

**Files:**

- Create: `src/k8sense/subagents/event_triager.py`
- Create: `tests/unit/test_subagents.py`

- [ ] **Step 1: Write the failing test for event_triager**

Create `tests/unit/test_subagents.py`:

```python
"""Subagent definitions — each AgentDefinition's prompt and metadata."""
import pytest

from claude_agent_sdk import AgentDefinition

from k8sense.subagents.event_triager import DEFINITION as ET_DEFINITION


def test_event_triager_is_agent_definition():
    assert isinstance(ET_DEFINITION, AgentDefinition)


def test_event_triager_description_mentions_events_and_warnings():
    desc = ET_DEFINITION.description
    assert "event" in desc.lower()
    assert "warning" in desc.lower() or "severity" in desc.lower()


def test_event_triager_prompt_includes_kubectl_get_events():
    assert "kubectl get events" in ET_DEFINITION.prompt


def test_event_triager_prompt_mentions_sort_by_timestamp():
    # The conventions block teaches the agent to sort by lastTimestamp
    assert "--sort-by" in ET_DEFINITION.prompt
    assert "lastTimestamp" in ET_DEFINITION.prompt


def test_event_triager_prompt_explains_no_warnings_case():
    # The agent should not fabricate concern when there are no warnings
    prompt = ET_DEFINITION.prompt.lower()
    assert "no warning" in prompt or "say so" in prompt or "don't fabricate" in prompt


def test_event_triager_uses_kubectl_tool_only():
    assert ET_DEFINITION.tools == ["mcp__k8sense__kubectl"]


def test_event_triager_has_reasonable_turn_budget():
    assert ET_DEFINITION.maxTurns == 8


def test_event_triager_runs_in_background_for_parallel_dispatch():
    assert ET_DEFINITION.background is True


def test_event_triager_inherits_orchestrator_model():
    assert ET_DEFINITION.model == "inherit"
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_subagents.py -v
```

Expected: `ImportError: No module named 'k8sense.subagents'` (the directory has no `__init__.py` yet either).

- [ ] **Step 3: Create the package init**

Create `src/k8sense/subagents/__init__.py`:

```python
"""Subagent definitions for k8sense Phase 2."""
from k8sense.subagents.event_triager import DEFINITION as event_triager_definition

__all__ = ["event_triager_definition"]
```

- [ ] **Step 4: Create the event_triager module**

Create `src/k8sense/subagents/event_triager.py`:

```python
"""event_triager — scans recent Kubernetes events and ranks by severity."""
from __future__ import annotations

from claude_agent_sdk import AgentDefinition

DESCRIPTION = (
    "Scans recent Kubernetes events in a given namespace (or cluster-wide) and "
    "ranks the most concerning ones by severity. Use when the user asks "
    "'what's going wrong', 'recent events', or 'any warnings'."
)

PROMPT = """You are the event_triager subagent for the homelab-k3s cluster.

Your role: investigate cluster events and surface the most concerning ones.
You have one tool: `kubectl`. Allowed verbs are read-only.

Conventions:
- Run `kubectl get events --sort-by=.lastTimestamp -A` (or `-n <namespace>` if scoped).
- When the user asks for "warnings" or "errors", filter with
  `--field-selector type=Warning`.
- Summarise the top 5 events by recency. For each, include:
  reason, count, firstTimestamp, lastTimestamp, and the object kind/name.
- Use the topology snapshot to disambiguate workloads if needed.
- If no Warning events are found, say so explicitly — do not fabricate concern.

Be concise: a bullet list is usually the right shape for the final answer.
"""

DEFINITION = AgentDefinition(
    description=DESCRIPTION,
    prompt=PROMPT,
    tools=["mcp__k8sense__kubectl"],
    model="inherit",
    maxTurns=8,
    background=True,
)
```

- [ ] **Step 5: Run the test — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_subagents.py -v
```

Expected: All 9 tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_subagents.py src/k8sense/subagents/__init__.py src/k8sense/subagents/event_triager.py
git commit -m "Add event_triager subagent with prompt-assertion tests"
```

---

## Task 8: log_investigator subagent (TDD)

**Files:**

- Create: `src/k8sense/subagents/log_investigator.py`
- Modify: `src/k8sense/subagents/__init__.py`
- Modify: `tests/unit/test_subagents.py`

- [ ] **Step 1: Append failing tests for log_investigator**

Append to `tests/unit/test_subagents.py`:

```python


from k8sense.subagents.log_investigator import DEFINITION as LI_DEFINITION  # noqa: E402


def test_log_investigator_is_agent_definition():
    assert isinstance(LI_DEFINITION, AgentDefinition)


def test_log_investigator_description_mentions_logs_and_pods():
    desc = LI_DEFINITION.description.lower()
    assert "log" in desc
    assert "pod" in desc


def test_log_investigator_prompt_starts_with_describe():
    # Convention: start with describe to read events + restart history
    assert "kubectl describe pod" in LI_DEFINITION.prompt


def test_log_investigator_prompt_uses_tail_default():
    assert "--tail=200" in LI_DEFINITION.prompt


def test_log_investigator_prompt_knows_about_previous_flag():
    assert "--previous" in LI_DEFINITION.prompt


def test_log_investigator_recognises_common_error_patterns():
    prompt = LI_DEFINITION.prompt
    assert "OOMKilled" in prompt
    assert "ImagePullBackOff" in prompt
    assert "CrashLoopBackOff" in prompt


def test_log_investigator_quotes_concrete_log_lines():
    prompt = LI_DEFINITION.prompt.lower()
    assert "quote" in prompt or "concrete" in prompt


def test_log_investigator_uses_kubectl_only():
    assert LI_DEFINITION.tools == ["mcp__k8sense__kubectl"]


def test_log_investigator_has_turn_budget_and_parallel_settings():
    assert LI_DEFINITION.maxTurns == 8
    assert LI_DEFINITION.background is True
    assert LI_DEFINITION.model == "inherit"
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_subagents.py -v
```

Expected: 9 new test failures with `ImportError: cannot import name 'DEFINITION' from 'k8sense.subagents.log_investigator'`.

- [ ] **Step 3: Create the log_investigator module**

Create `src/k8sense/subagents/log_investigator.py`:

```python
"""log_investigator — fetches pod logs and explains restarts/crashes."""
from __future__ import annotations

from claude_agent_sdk import AgentDefinition

DESCRIPTION = (
    "Given a pod name and namespace, fetches logs and describe output to "
    "explain restarts, crashes, or anomalies. Use when the user asks "
    "'why is pod X failing', 'what's in pod X's logs', or 'why is X "
    "crashlooping'."
)

PROMPT = """You are the log_investigator subagent for the homelab-k3s cluster.

Your role: given a pod name and namespace, explain what's happening from its
logs and describe output.

You have one tool: `kubectl`. Allowed verbs are read-only.

Conventions:
- Start with `kubectl describe pod <name> -n <namespace>` to read events on
  the pod and restart history.
- Then `kubectl logs <name> -n <namespace> --tail=200`.
- If logs are empty (the pod just restarted), retry with `--previous` to get
  the prior container's logs.
- Recognise common patterns from describe output:
  * `OOMKilled` → memory limit hit, suggest checking limits.
  * `ImagePullBackOff` → image / registry / credentials.
  * `CrashLoopBackOff` with empty current logs → check `--previous`.
- Quote 2-3 concrete log lines in your final answer instead of paraphrasing.

Be concise: a short paragraph plus the quoted lines is usually the right shape.
"""

DEFINITION = AgentDefinition(
    description=DESCRIPTION,
    prompt=PROMPT,
    tools=["mcp__k8sense__kubectl"],
    model="inherit",
    maxTurns=8,
    background=True,
)
```

- [ ] **Step 4: Update the subagents package init**

Replace `src/k8sense/subagents/__init__.py` with:

```python
"""Subagent definitions for k8sense Phase 2."""
from k8sense.subagents.event_triager import DEFINITION as event_triager_definition
from k8sense.subagents.log_investigator import DEFINITION as log_investigator_definition

__all__ = [
    "event_triager_definition",
    "log_investigator_definition",
]
```

- [ ] **Step 5: Run the test — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_subagents.py -v
```

Expected: 18 tests pass (9 prior + 9 new).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_subagents.py src/k8sense/subagents/__init__.py src/k8sense/subagents/log_investigator.py
git commit -m "Add log_investigator subagent with describe+logs conventions"
```

---

## Task 9: metrics_analyst subagent (TDD)

**Files:**

- Create: `src/k8sense/subagents/metrics_analyst.py`
- Modify: `src/k8sense/subagents/__init__.py`
- Modify: `tests/unit/test_subagents.py`

- [ ] **Step 1: Append failing tests for metrics_analyst**

Append to `tests/unit/test_subagents.py`:

```python


from k8sense.subagents.metrics_analyst import DEFINITION as MA_DEFINITION  # noqa: E402


def test_metrics_analyst_is_agent_definition():
    assert isinstance(MA_DEFINITION, AgentDefinition)


def test_metrics_analyst_description_mentions_resource_usage():
    desc = MA_DEFINITION.description.lower()
    assert "cpu" in desc or "memory" in desc or "resource" in desc
    assert "trend" in desc or "historical" in desc or "prometheus" in desc.lower()


def test_metrics_analyst_has_both_kubectl_and_prometheus():
    assert "mcp__k8sense__kubectl" in MA_DEFINITION.tools
    assert "mcp__k8sense__prometheus_query" in MA_DEFINITION.tools
    assert len(MA_DEFINITION.tools) == 2


def test_metrics_analyst_prompt_distinguishes_snapshot_vs_trend():
    prompt = MA_DEFINITION.prompt
    assert "kubectl top" in prompt
    assert "prometheus_query" in prompt
    assert "lookback" in prompt


def test_metrics_analyst_prompt_includes_promql_examples():
    prompt = MA_DEFINITION.prompt
    assert "container_cpu_usage_seconds_total" in prompt
    assert "container_memory_working_set_bytes" in prompt


def test_metrics_analyst_prompt_explains_prometheus_fallback():
    prompt = MA_DEFINITION.prompt.lower()
    assert "unreachable" in prompt or "fall back" in prompt or "fallback" in prompt


def test_metrics_analyst_has_turn_budget_and_parallel_settings():
    assert MA_DEFINITION.maxTurns == 8
    assert MA_DEFINITION.background is True
    assert MA_DEFINITION.model == "inherit"
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_subagents.py -v
```

Expected: 7 new failures with `ImportError`.

- [ ] **Step 3: Create the metrics_analyst module**

Create `src/k8sense/subagents/metrics_analyst.py`:

```python
"""metrics_analyst — kubectl top for snapshots, PromQL for trends."""
from __future__ import annotations

from claude_agent_sdk import AgentDefinition

DESCRIPTION = (
    "Queries kubectl top and Prometheus for resource usage of pods, nodes, or "
    "workloads. Use for 'how much CPU/memory is X using', 'is anything near "
    "its limit', or historical trends ('how has CPU trended over the last "
    "hour')."
)

PROMPT = """You are the metrics_analyst subagent for the homelab-k3s cluster.

Your role: answer questions about resource usage. You decide whether the
question wants a snapshot (current value) or a trend (over time), then pick
the right tool.

You have two tools:
- `kubectl` — for the current snapshot via `kubectl top pods` / `kubectl top nodes`.
- `prometheus_query` — for trends and historical data via PromQL.

Conventions:
- For current-state questions ("how much memory is X using right now") → use
  `kubectl top pods -n <ns>` or `kubectl top nodes`.
- For trend questions ("how has CPU trended", "over the last hour") → use
  `prometheus_query` with a `lookback` like '5m', '1h', '24h'.
- Useful PromQL primitives:
  * pod CPU rate (2-min window):
      sum(rate(container_cpu_usage_seconds_total{namespace="X"}[2m])) by (pod)
  * pod memory snapshot:
      container_memory_working_set_bytes{namespace="X"}
  * node load:
      node_load1
  * restart counter:
      kube_pod_container_status_restarts_total
- If Prometheus is unreachable (the tool returns exit_code=-1 with
  "unreachable" in stderr), fall back to `kubectl top` and say so in your
  final answer.

Be concise. Quote concrete numbers, not generic statements.
"""

DEFINITION = AgentDefinition(
    description=DESCRIPTION,
    prompt=PROMPT,
    tools=["mcp__k8sense__kubectl", "mcp__k8sense__prometheus_query"],
    model="inherit",
    maxTurns=8,
    background=True,
)
```

- [ ] **Step 4: Update the subagents package init**

Replace `src/k8sense/subagents/__init__.py` with:

```python
"""Subagent definitions for k8sense Phase 2."""
from k8sense.subagents.event_triager import DEFINITION as event_triager_definition
from k8sense.subagents.log_investigator import DEFINITION as log_investigator_definition
from k8sense.subagents.metrics_analyst import DEFINITION as metrics_analyst_definition

__all__ = [
    "event_triager_definition",
    "log_investigator_definition",
    "metrics_analyst_definition",
]
```

- [ ] **Step 5: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_subagents.py -v
```

Expected: 25 tests pass (18 prior + 7 new).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_subagents.py src/k8sense/subagents/__init__.py src/k8sense/subagents/metrics_analyst.py
git commit -m "Add metrics_analyst subagent (kubectl top + Prometheus)"
```

---

## Task 10: Wire subagents into agent.build_options

**Files:**

- Modify: `src/k8sense/agent.py`
- Modify: `tests/unit/test_agent_helpers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_agent_helpers.py`:

```python


def test_build_options_includes_three_subagents():
    options = build_options("SYS", model_id="claude-sonnet-4-6")
    agents = getattr(options, "agents", None)
    assert agents is not None
    assert set(agents.keys()) == {
        "event_triager",
        "log_investigator",
        "metrics_analyst",
    }
    # Each should be an AgentDefinition
    from claude_agent_sdk import AgentDefinition
    for name, agent in agents.items():
        assert isinstance(agent, AgentDefinition), f"{name} is not AgentDefinition"
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_agent_helpers.py::test_build_options_includes_three_subagents -v
```

Expected: FAIL — `agents` is None.

- [ ] **Step 3: Update `build_options` to include agents**

In `src/k8sense/agent.py`, find the existing `build_options` and replace with:

```python
def build_options(system_prompt: str, model_id: str = DEFAULT_MODEL) -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions wired with kubectl + prometheus tools and subagents."""
    server = create_sdk_mcp_server(
        name="k8sense",
        version="0.2.0",
        tools=[kubectl_tool, prometheus_tool],
    )
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"k8sense": server},
        allowed_tools=[
            "mcp__k8sense__kubectl",
            "mcp__k8sense__prometheus_query",
        ],
        agents={
            "event_triager": event_triager_definition,
            "log_investigator": log_investigator_definition,
            "metrics_analyst": metrics_analyst_definition,
        },
        model=model_id,
    )
```

Add the subagent imports near the top of `agent.py`. Find the existing import block:

```python
from k8sense.tools.kubectl import kubectl_tool
from k8sense.tools.prometheus import prometheus_tool
```

Add right after:

```python
from k8sense.subagents import (
    event_triager_definition,
    log_investigator_definition,
    metrics_analyst_definition,
)
```

- [ ] **Step 4: Run the test — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_agent_helpers.py -v
```

Expected: All tests pass including the new subagent wiring test.

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~122 passed, 1 skipped. (Exact number depends on per-task TDD-fix commits along the way. The important assertion is: no regressions, and the new `test_build_options_includes_three_subagents` passes.)

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/agent.py tests/unit/test_agent_helpers.py
git commit -m "Wire three subagents into build_options"
```

---

## Task 11: Orchestrator delegation prompt addendum

**Files:**

- Modify: `src/k8sense/prompts/system.py`
- Modify: `tests/unit/test_system_prompt.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_system_prompt.py`:

```python


def test_prompt_mentions_specialised_subagents():
    prompt = build_system_prompt_from_topology("")
    # The delegation paragraph should mention each subagent by name
    assert "event_triager" in prompt
    assert "log_investigator" in prompt
    assert "metrics_analyst" in prompt


def test_prompt_explains_when_to_dispatch_subagents():
    prompt = build_system_prompt_from_topology("").lower()
    assert "delegate" in prompt or "dispatch" in prompt


def test_prompt_explains_when_NOT_to_dispatch():
    prompt = build_system_prompt_from_topology("").lower()
    # For simple direct questions, use kubectl yourself — should be in the prompt
    assert "simple" in prompt or "directly" in prompt or "yourself" in prompt
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
.venv/bin/pytest tests/unit/test_system_prompt.py -v
```

Expected: 3 new failures (subagent names missing from prompt).

- [ ] **Step 3: Add the delegation paragraph to the template**

Edit `src/k8sense/prompts/system.py`. The current `_TEMPLATE` ends with the conventions block followed by the topology snapshot section. Insert the new paragraph BEFORE the `Cluster topology snapshot` line.

Find the current `_TEMPLATE` definition. Add this new paragraph between the existing "Conventions" block and the "Cluster topology snapshot" line:

```
You have specialised investigators available as subagents. Delegate to them when a question is narrow enough to fit one of their descriptions:
- event_triager — for cluster events and warnings
- log_investigator — for pod-specific log questions
- metrics_analyst — for resource-usage and trend questions
For broad multi-source questions (e.g. "give me a health summary"), dispatch multiple subagents in parallel and merge their findings. For simple direct questions ("list namespaces", "describe deployment X"), just use kubectl yourself.

```

The full updated `_TEMPLATE` should look like (replace the existing one):

```python
_TEMPLATE = """You are k8sense, a careful and methodical SRE for the homelab-k3s Kubernetes cluster.

Your job is to investigate questions about the cluster by running read-only kubectl commands through the `kubectl` tool, then synthesise a clear explanation in plain English.

You have exactly one tool: `kubectl`. It accepts a list of arguments. Allowed verbs: get, describe, logs, top, events, version. Mutating verbs are rejected.

You MUST NOT attempt mutating verbs (delete, apply, create, scale, patch, edit, exec, rollout). The tool will refuse them, but you should not try.

Conventions:
- Always run at least one kubectl command. If the question is purely conceptual, run `kubectl version` to confirm the cluster is reachable, then answer.
- Use namespaces, pod names, and resource kinds from the topology snapshot below.
- Prefer specific invocations (e.g. `kubectl describe pod X -n Y`) over broad sweeps.
- If a tool call fails, read the stderr and either retry with adjusted args or explain why you cannot continue.
- Be concise in your final answer. Prefer bullet points for multi-part findings.

You have specialised investigators available as subagents. Delegate to them when a question is narrow enough to fit one of their descriptions:
- event_triager — for cluster events and warnings
- log_investigator — for pod-specific log questions
- metrics_analyst — for resource-usage and trend questions
For broad multi-source questions (e.g. "give me a health summary"), dispatch multiple subagents in parallel and merge their findings. For simple direct questions ("list namespaces", "describe deployment X"), just use kubectl yourself.

Cluster topology snapshot (captured at startup):
{topology}
"""
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_system_prompt.py -v
```

Expected: All system-prompt tests pass.

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: All prior tests + 3 new pass. No regressions.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/prompts/system.py tests/unit/test_system_prompt.py
git commit -m "Add subagent delegation paragraph to system prompt"
```

---

## Task 12: Renderer subagent_dispatch method

**Files:**

- Modify: `src/k8sense/render.py`
- Modify: `tests/unit/test_render.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_render.py`:

```python


def test_subagent_dispatch_prints_name_and_brief(captured):
    renderer, console = captured
    renderer.subagent_dispatch("log_investigator", "investigate argocd-server restarts")
    output = console.export_text()
    assert "log_investigator" in output
    assert "investigate argocd-server restarts" in output


def test_subagent_dispatch_uses_dispatch_marker(captured):
    renderer, console = captured
    renderer.subagent_dispatch("event_triager", "scan warnings")
    output = console.export_text()
    # The marker glyph signals dispatch visually
    assert "↳" in output or "dispatching" in output.lower()
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_render.py::test_subagent_dispatch_prints_name_and_brief -v
```

Expected: FAIL — `Renderer` has no `subagent_dispatch` attribute.

- [ ] **Step 3: Add the method to `Renderer`**

In `src/k8sense/render.py`, add a new method to the `Renderer` class. Insert it after `tool_call`:

```python
    def subagent_dispatch(self, name: str, brief: str) -> None:
        """Render a subagent dispatch marker."""
        self.console.print(
            Text(f"↳ dispatching {name}: {brief}", style="bold cyan")
        )
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_render.py -v
```

Expected: All render tests pass (38 prior + 2 new = 40).

- [ ] **Step 5: Commit**

```bash
git add src/k8sense/render.py tests/unit/test_render.py
git commit -m "Add Renderer.subagent_dispatch() for streamed dispatch visibility"
```

---

## Task 13: Detect subagent dispatch in the agent loop

**Files:**

- Modify: `src/k8sense/agent.py`
- Modify: `tests/unit/test_agent_helpers.py`

The SDK exposes subagent dispatch as a tool call to a special tool (presumed `Task`). We detect that in `_dispatch_message` and route it to `Renderer.subagent_dispatch` instead of the normal `tool_call` rendering.

- [ ] **Step 1: Inspect the SDK to confirm the dispatch tool name**

Run:

```bash
.venv/bin/python -c "
import claude_agent_sdk as sdk
# Look for anything related to Task/Subagent dispatch
public = [n for n in dir(sdk) if not n.startswith('_')]
print('Public:', sorted(public))
# Hint: the SDK likely names this 'Task' but we verify
"
```

Quote the output in your report. The dispatch tool is conventionally called `Task` and its input has `subagent_type` plus `prompt`/`description`. If the actual name differs, adjust the constant below.

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_agent_helpers.py`:

```python


from k8sense.agent import (  # noqa: E402
    SUBAGENT_DISPATCH_TOOL_NAME,
    is_subagent_dispatch,
    extract_subagent_dispatch,
)


def test_is_subagent_dispatch_detects_task_tool():
    assert is_subagent_dispatch(SUBAGENT_DISPATCH_TOOL_NAME) is True


def test_is_subagent_dispatch_rejects_kubectl():
    assert is_subagent_dispatch("mcp__k8sense__kubectl") is False


def test_extract_subagent_dispatch_pulls_name_and_brief():
    block_input = {
        "subagent_type": "log_investigator",
        "description": "investigate argocd-server",
    }
    name, brief = extract_subagent_dispatch(block_input)
    assert name == "log_investigator"
    assert brief == "investigate argocd-server"


def test_extract_subagent_dispatch_uses_prompt_if_no_description():
    block_input = {
        "subagent_type": "event_triager",
        "prompt": "scan warnings in argocd",
    }
    name, brief = extract_subagent_dispatch(block_input)
    assert name == "event_triager"
    assert "scan warnings" in brief


def test_extract_subagent_dispatch_truncates_long_brief():
    long = "x" * 500
    block_input = {"subagent_type": "et", "description": long}
    _, brief = extract_subagent_dispatch(block_input)
    assert len(brief) <= 120
```

- [ ] **Step 3: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_agent_helpers.py -v
```

Expected: 5 failures with `ImportError`.

- [ ] **Step 4: Add the detection helpers and update `_dispatch_message`**

In `src/k8sense/agent.py`, add near the top (after the existing `_EXIT_CODE_RE` line):

```python
SUBAGENT_DISPATCH_TOOL_NAME = "Task"  # SDK names the dispatch primitive "Task"
_BRIEF_MAX_LEN = 120


def is_subagent_dispatch(tool_name: str) -> bool:
    """Return True if the tool name signals a subagent dispatch (not a normal tool call)."""
    return tool_name == SUBAGENT_DISPATCH_TOOL_NAME


def extract_subagent_dispatch(tool_input: dict) -> tuple[str, str]:
    """Pull (subagent_name, brief description) from a Task tool call input.

    Brief prefers `description` over `prompt`, truncated to a renderer-friendly length.
    """
    name = tool_input.get("subagent_type", "<unknown>")
    brief = tool_input.get("description") or tool_input.get("prompt") or ""
    if len(brief) > _BRIEF_MAX_LEN:
        brief = brief[: _BRIEF_MAX_LEN - 1] + "…"
    return name, brief
```

Then update `_dispatch_message`'s `AssistantMessage` branch. Find the existing block:

```python
elif isinstance(block, ToolUseBlock):
    if not budget.charge():
        renderer.error(f"hit max tool calls ({budget.limit})")
        return False
    renderer.tool_call(block.name, block.input)
```

Replace with:

```python
elif isinstance(block, ToolUseBlock):
    if not budget.charge():
        renderer.error(f"hit max tool calls ({budget.limit})")
        return False
    if is_subagent_dispatch(block.name):
        name, brief = extract_subagent_dispatch(block.input or {})
        renderer.subagent_dispatch(name, brief)
    else:
        renderer.tool_call(block.name, block.input)
```

- [ ] **Step 5: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_agent_helpers.py -v
```

Expected: All agent helper tests pass.

- [ ] **Step 6: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: no regressions; new tests added.

- [ ] **Step 7: Commit**

```bash
git add src/k8sense/agent.py tests/unit/test_agent_helpers.py
git commit -m "Route subagent dispatches to Renderer.subagent_dispatch in the agent loop"
```

---

## Task 14: Add subagent_called fingerprint type to the eval scorer

**Files:**

- Modify: `evals/runner.py`
- Modify: `tests/unit/test_eval_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_eval_runner.py`:

```python


def test_subagent_called_passes_when_dispatch_recorded():
    case = EvalCase(id="t14", question="?", fingerprints=[
        {"type": "subagent_called", "value": "log_investigator"},
    ])
    result = _result(tool_calls=[
        {"name": "Task", "input": {"subagent_type": "log_investigator", "description": "x"}},
    ])
    passes, _ = score_fingerprints(case, result)
    assert passes is True


def test_subagent_called_fails_when_no_dispatch():
    case = EvalCase(id="t15", question="?", fingerprints=[
        {"type": "subagent_called", "value": "metrics_analyst"},
    ])
    result = _result(tool_calls=[
        {"name": "mcp__k8sense__kubectl", "input": {"args": ["get", "pods"]}},
    ])
    passes, failures = score_fingerprints(case, result)
    assert passes is False
    assert "subagent 'metrics_analyst'" in failures[0]


def test_subagent_called_fails_when_different_subagent_dispatched():
    case = EvalCase(id="t16", question="?", fingerprints=[
        {"type": "subagent_called", "value": "log_investigator"},
    ])
    result = _result(tool_calls=[
        {"name": "Task", "input": {"subagent_type": "event_triager", "description": "x"}},
    ])
    passes, failures = score_fingerprints(case, result)
    assert passes is False


def test_subagent_called_handles_missing_input_safely():
    case = EvalCase(id="t17", question="?", fingerprints=[
        {"type": "subagent_called", "value": "event_triager"},
    ])
    # A malformed tool_call with no input shouldn't crash the scorer
    result = _result(tool_calls=[{"name": "Task"}])
    passes, failures = score_fingerprints(case, result)
    assert passes is False
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
.venv/bin/pytest tests/unit/test_eval_runner.py -v
```

Expected: 4 new failures — `score_fingerprints` doesn't know `subagent_called`.

- [ ] **Step 3: Add the new fingerprint branch to `score_fingerprints`**

In `evals/runner.py`, find the existing `score_fingerprints` function. Add a new `elif` branch BEFORE the final `else` (unknown fingerprint type):

```python
        elif kind == "subagent_called":
            ok = any(
                tc.get("name") == "Task"
                and ((tc.get("input") or {}).get("subagent_type") == value)
                for tc in result.tool_calls
            )
            if not ok:
                failures.append(f"subagent '{value}' was never dispatched")
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_eval_runner.py -v
```

Expected: All eval runner tests pass.

- [ ] **Step 5: Commit**

```bash
git add evals/runner.py tests/unit/test_eval_runner.py
git commit -m "Add subagent_called fingerprint type to eval scorer"
```

---

## Task 15: Expand eval dataset with 5 multi-source entries

**Files:**

- Modify: `evals/dataset.jsonl`

- [ ] **Step 1: Append 5 new entries**

Append these lines to `evals/dataset.jsonl` (each is one line of JSON):

```jsonl
{"id": "events-argocd", "question": "Are there any recent warning events in the argocd namespace?", "fingerprints": [{"type": "subagent_called", "value": "event_triager"}, {"type": "substring", "value": "argocd"}]}
{"id": "why-restarts", "question": "Why has the argocd-server pod been restarting?", "fingerprints": [{"type": "subagent_called", "value": "log_investigator"}, {"type": "tool_args_contains", "value": "argocd-server"}]}
{"id": "top-memory-monitoring", "question": "Which pod in the monitoring namespace is using the most memory right now?", "fingerprints": [{"type": "subagent_called", "value": "metrics_analyst"}, {"type": "tool_args_contains", "value": "monitoring"}]}
{"id": "cpu-trend-30m", "question": "Has CPU usage on the cluster trended up over the last 30 minutes?", "fingerprints": [{"type": "subagent_called", "value": "metrics_analyst"}, {"type": "regex", "value": "\\d+(\\.\\d+)?\\s*(%|cores?)"}]}
{"id": "cluster-health-summary", "question": "Give a one-paragraph health summary covering events, logs of the busiest pod, and current resource usage.", "fingerprints": [{"type": "subagent_called", "value": "event_triager"}, {"type": "subagent_called", "value": "log_investigator"}, {"type": "subagent_called", "value": "metrics_analyst"}]}
```

- [ ] **Step 2: Verify the dataset loads**

Run:

```bash
.venv/bin/python -c "from pathlib import Path; from evals.runner import load_dataset; cases = load_dataset(Path('evals/dataset.jsonl')); print(f'loaded {len(cases)} cases'); print([c.id for c in cases][-5:])"
```

Expected: `loaded 15 cases` and the last 5 ids match those above.

- [ ] **Step 3: Confirm tests still pass**

Run:

```bash
.venv/bin/pytest -v
```

Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add evals/dataset.jsonl
git commit -m "Expand eval dataset with 5 multi-source subagent questions"
```

---

## Task 16: Phase 2 smoke test against the real cluster

**Files:**

- Modify: `tests/smoke/test_real_cluster.py`

- [ ] **Step 1: Append a multi-source smoke test**

Append to `tests/smoke/test_real_cluster.py`:

```python


@pytest.mark.smoke
def test_health_summary_dispatches_multiple_subagents():
    if not os.environ.get("K8SENSE_ALLOW_API"):
        pytest.skip("smoke test requires K8SENSE_ALLOW_API=1")
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        # OAuth via claude CLI is the default; explicit token works too.
        # If neither, the SDK will use the local claude CLI's login (Phase 1 verified).
        pass

    console = Console(record=True, force_terminal=False, width=120)
    renderer = Renderer(console=console)
    # The question forces a multi-source investigation
    exit_code = asyncio.run(
        run_ask(
            "give a one-paragraph health summary covering events, logs of the "
            "busiest pod, and current resource usage",
            renderer,
        )
    )
    output = console.export_text()

    assert exit_code == 0, f"agent exited {exit_code}\n{output}"
    # At least two distinct subagent dispatches must have fired
    dispatched = {
        agent
        for agent in ("event_triager", "log_investigator", "metrics_analyst")
        if f"dispatching {agent}" in output
    }
    assert len(dispatched) >= 2, (
        f"expected ≥2 subagents to dispatch, got: {dispatched}\n\n{output}"
    )
```

- [ ] **Step 2: Verify the default suite still excludes smoke tests**

Run:

```bash
.venv/bin/pytest -v
```

Expected: smoke tests are skipped (because `K8SENSE_ALLOW_API` is not set in CI / default runs).

- [ ] **Step 3: Verify smoke discovery**

Run:

```bash
.venv/bin/pytest -m smoke --collect-only
```

Expected: both smoke tests visible — the Phase 1 namespace test AND the new multi-source one.

- [ ] **Step 4: Commit**

```bash
git add tests/smoke/test_real_cluster.py
git commit -m "Add Phase 2 multi-source smoke test"
```

---

## Task 17: README polish + Phase 2 tag

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Update the README's "Status" section**

In `README.md`, find:

```markdown
## Status

**Phase 1 (current):** `k8sense ask "<question>"` — one-shot investigation CLI.
```

Change to:

```markdown
## Status

**Phase 2 (current):** `k8sense ask "<question>"` with parallel subagent dispatch.

- `event_triager` — recent events triage
- `log_investigator` — pod log root-cause analysis
- `metrics_analyst` — kubectl top + PromQL trends
```

- [ ] **Step 2: Add an Authentication / Prometheus note to the README**

Right after the "## Authentication" section, append:

```markdown
## Prometheus access

metrics_analyst queries PromQL against the homelab Prometheus instance.
By default it talks to `http://192.168.70.174:9090`. Override via the
`K8SENSE_PROM_URL` env var if running from outside the homelab LAN.

If Prometheus is unreachable, metrics_analyst automatically falls back to
`kubectl top` for current-state queries.
```

- [ ] **Step 3: Update the Architecture section to mention subagents and prometheus**

In the existing Architecture bullets, add after `tools/kubectl.py`:

```markdown
- `src/k8sense/tools/prometheus.py` — async PromQL client (instant + range).
- `src/k8sense/subagents/` — three specialised investigators (event_triager, log_investigator, metrics_analyst).
```

- [ ] **Step 4: Run the full test suite one more time**

Run:

```bash
.venv/bin/pytest -v
```

Expected: all tests pass (units + 2 smoke skipped).

- [ ] **Step 5: Run the live eval suite as final verification**

Run:

```bash
.venv/bin/python -m evals.runner
cat evals/report.md
```

Expected: ≥13 of 15 pass (10 Phase-1 carryover + ≥3 of the 5 new multi-source entries). If a subagent dispatch fingerprint fails, that's a real signal worth fixing — adjust the orchestrator delegation prompt or the subagent description as needed before merging. Do not loosen fingerprints to mask failures.

- [ ] **Step 6: Commit README**

```bash
git add README.md
git commit -m "Phase 2 complete: ship subagents + Prometheus tool + multi-source evals"
```

- [ ] **Step 7: Tag the phase**

```bash
git tag -a phase-2 -m "Phase 2: three subagents + Prometheus tool + multi-source eval expansion"
git log --oneline -5
```

Expected: `phase-2` tag points at the latest commit.

---

## Phase 2 acceptance checklist

After all tasks are complete, verify:

- [ ] `k8sense ask "give a cluster health summary"` dispatches ≥2 subagents (visible via `↳ dispatching <name>: ...` in the output).
- [ ] `k8sense ask "why is argocd-server restarting?"` invokes `log_investigator` and produces an answer that quotes concrete log lines.
- [ ] `k8sense ask "how has CPU trended over the last hour"` invokes `metrics_analyst` which uses `prometheus_query` with a lookback.
- [ ] `k8sense doctor` still reports three greens.
- [ ] `pytest` passes (all 120+ unit tests).
- [ ] `K8SENSE_ALLOW_API=1 pytest -m smoke` runs both smoke tests against the real cluster.
- [ ] `python -m evals.runner` writes a report showing ≥13/15 pass (Phase 1's 10 + ≥3 of the 5 new).
- [ ] Git log reads as a clean TDD progression — each subagent has its own test+impl pair.
- [ ] No mocking of `kubectl`, Prometheus, or the SDK anywhere in the test suite.

If all boxes are ticked, Phase 2 is shippable and we can write the Phase 3 plan (MCP server).
