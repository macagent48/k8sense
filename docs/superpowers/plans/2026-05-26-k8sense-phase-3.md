# k8sense Phase 3 Implementation Plan (MCP Server)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose k8sense as a stdio MCP server (`k8sense mcp`) exposing two tools (kubectl, prometheus_query), three live resources (topology, manifests/{namespace}, events/recent), and three workflow prompts (investigate-pod, triage-events, metrics). Both `k8sense ask` (in-process) and `k8sense mcp` (stdio) consume the same `tools/registry.py` so behaviour stays consistent.

**Architecture:** A new `tools/registry.py` is the single source of truth for the two tools (name, description, Pydantic input model, handler). The existing `agent.build_options()` and the new `mcp_server/server.py` both iterate this registry and wrap the same handlers in their respective registration shells. The MCP server is built on `mcp.server.lowlevel.Server` and exposes tools + resources + prompts; the CLI adds an `mcp` subcommand that runs it over stdio. Pydantic-derived JSON Schemas replace today's bare dict schemas in both transports.

**Tech Stack:**

- Phase 2 stack (`claude-agent-sdk`, `rich`, `httpx`, `pytest`, `python-dotenv`)
- New: `pydantic` (already pulled in as a transitive dep of `mcp`/`claude-agent-sdk`)
- New: `mcp` package (already a transitive dep; no pyproject.toml change needed)

**Spec:** `docs/superpowers/specs/2026-05-26-k8sense-phase-3-design.md`
**Predecessor:** Phase 2 at git tag `phase-2` (commit `0fdf82a`); current `HEAD` at `890fb6b` (spec commit)

**Testing discipline:** Inherits Phase 1/2's "strict TDD for deterministic code, eval harness for LLM behaviour." No mocking of kubectl, Prometheus, the SDK, or the MCP server. The "kubectl missing" tests use `monkeypatch.setenv("PATH", str(tmp_path))` — the same env-manipulation pattern.

**SDK reality verified at plan time:**

- `mcp.server.Server` re-exports `mcp.server.lowlevel.server.Server`. The lowlevel import path also works.
- `Server` has decorators: `list_tools`, `call_tool`, `list_resources`, `list_resource_templates`, `read_resource`, `list_prompts`, `get_prompt` — all snake_case.
- `mcp.server.stdio.stdio_server` is the async context manager that yields `(read_stream, write_stream)`.
- `mcp.types` provides Pydantic models: `Tool`, `Resource`, `ResourceTemplate`, `Prompt`, `PromptArgument`, `TextContent`, `GetPromptResult`, `PromptMessage`.
- `read_resource` handlers return `str | bytes | Iterable[ReadResourceContents]`; a plain string is the simplest path.

---

## File Structure

```
new-project/
├── src/k8sense/
│   ├── cli.py                       # MODIFY: +1 subcommand `mcp`
│   ├── agent.py                     # MODIFY: build_options() iterates registry
│   ├── tools/
│   │   ├── kubectl.py               # unchanged
│   │   ├── prometheus.py            # unchanged
│   │   └── registry.py              # NEW: ToolSpec + Pydantic models + factory
│   └── mcp_server/                  # NEW package
│       ├── __init__.py
│       ├── server.py                # build_server() + run_stdio()
│       ├── resources.py             # 3 resources + DNS-1123 validation
│       └── prompts.py               # 3 prompts + dispatch
├── tests/
│   ├── unit/
│   │   ├── test_tool_registry.py    # NEW
│   │   ├── test_mcp_resources.py    # NEW
│   │   ├── test_mcp_prompts.py      # NEW
│   │   └── test_mcp_server.py       # NEW
│   └── smoke/
│       └── test_mcp_stdio.py        # NEW
├── pyproject.toml                   # MODIFY: bump version to 0.3.0
└── README.md                        # MODIFY: Phase 3 status + Claude Code config block
```

**Responsibilities:**

- `tools/registry.py` — `KubectlInput`, `PrometheusInput` Pydantic models; `ToolSpec` dataclass; `all_tool_specs()` factory. Single source of truth for tool metadata.
- `mcp_server/server.py` — `build_server()` returns a configured `mcp.server.Server`; `run_stdio()` is the async entrypoint that wires `stdio_server` to `server.run`. Imports `register_resources` and `register_prompts` from sibling modules.
- `mcp_server/resources.py` — `_topology_content()`, `_manifests_content(ns)`, `_recent_events_content()` content helpers; `_is_valid_namespace()` DNS-1123 validator; `register_resources(server)` attaches the three decorators.
- `mcp_server/prompts.py` — `_investigate_pod`, `_triage_events`, `_metrics` pure helpers; `_validate_namespace` raises on bad input; `register_prompts(server)` attaches the two decorators.
- `agent.py` — `build_options()` refactored to import `all_tool_specs` and the existing `tool` decorator from the SDK; no change to subagent wiring.
- `cli.py` — `mcp` subcommand simply calls `asyncio.run(run_stdio())`.

---

## Task 1: Bump version to 0.3.0

**Files:**

- Modify: `pyproject.toml`

- [ ] **Step 1: Bump the project version**

Find the existing `version` line in `pyproject.toml`:

```toml
version = "0.1.0"
```

Change to:

```toml
version = "0.3.0"
```

(Phase 2 didn't bump the version. Phase 3 jumps to `0.3.0` to align with the in-process MCP server version we'll set in `build_options`.)

- [ ] **Step 2: Re-install editable so `k8sense --version` (if anything reads it) and the script entry are refreshed**

Run:

```bash
.venv/bin/pip install -e ".[dev]" --quiet
```

Expected: succeeds. (No new deps; this just refreshes the editable metadata.)

- [ ] **Step 3: Confirm all tests still pass**

Run:

```bash
.venv/bin/pytest -v
```

Expected: 144 passed, 2 skipped.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "Bump to 0.3.0 for Phase 3"
```

---

## Task 2: Tool registry — Pydantic input models (TDD)

**Files:**

- Create: `src/k8sense/tools/registry.py`
- Create: `tests/unit/test_tool_registry.py`

- [ ] **Step 1: Write the failing test for the Pydantic input models**

Create `tests/unit/test_tool_registry.py`:

```python
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
```

- [ ] **Step 2: Run the tests — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_tool_registry.py -v
```

Expected: `ImportError: No module named 'k8sense.tools.registry'`.

- [ ] **Step 3: Write the minimal `registry.py` (just the Pydantic models)**

Create `src/k8sense/tools/registry.py`:

```python
"""Tool registry — shared by the in-process SDK transport and the stdio MCP transport.

Defines:
- Pydantic input models (KubectlInput, PrometheusInput)
- ToolSpec dataclass (transport-agnostic tool description)
- all_tool_specs() factory
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field


class KubectlInput(BaseModel):
    args: list[str] = Field(
        min_length=1,
        description=(
            "kubectl argv. Allowed first verb: get|describe|logs|top|events|version. "
            'Example: ["get", "pods", "-n", "argocd"].'
        ),
    )


class PrometheusInput(BaseModel):
    query: str = Field(min_length=1, description="PromQL expression")
    lookback: str | None = Field(
        default=None,
        pattern=r"^\d+[smhd]$",
        description="Range window (e.g. '5m', '1h', '24h'). Omit for instant query.",
    )


@dataclass
class ToolSpec:
    """Transport-agnostic description of one k8sense tool."""

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
```

(The `all_tool_specs()` factory is added in the next task; we're TDD-ing the Pydantic models first.)

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_tool_registry.py -v
```

Expected: all 9 tests pass (1 + 2 + 1 + 1 + 1 + 5 parametrized + 1 + 1 = 13 actually — count parametrize cases).

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~157 pass (144 prior + 13 new), 2 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/tools/registry.py tests/unit/test_tool_registry.py
git commit -m "Add Pydantic input models for kubectl and prometheus tools"
```

---

## Task 3: Tool registry — ToolSpec factory (TDD)

**Files:**

- Modify: `src/k8sense/tools/registry.py`
- Modify: `tests/unit/test_tool_registry.py`

- [ ] **Step 1: Append failing tests for `all_tool_specs()`**

Append to `tests/unit/test_tool_registry.py`:

```python


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
```

- [ ] **Step 2: Run the tests — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_tool_registry.py -v
```

Expected: 5 failures with `ImportError: cannot import name 'all_tool_specs' from 'k8sense.tools.registry'`.

- [ ] **Step 3: Implement `all_tool_specs()`**

Append to `src/k8sense/tools/registry.py`:

```python


from k8sense.tools.kubectl import kubectl_handler
from k8sense.tools.prometheus import prometheus_handler


def all_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="kubectl",
            description=(
                "Run a READ-ONLY kubectl command against the homelab-k3s cluster. "
                "Allowed verbs: get, describe, logs, top, events, version. "
                "Returns stdout, stderr, and exit_code."
            ),
            input_model=KubectlInput,
            handler=kubectl_handler,
        ),
        ToolSpec(
            name="prometheus_query",
            description=(
                "Query PromQL against the homelab Prometheus instance. "
                "Instant query by default; pass `lookback` ('5m', '1h', '24h') for range. "
                "Read-only — no mutating PromQL operations."
            ),
            input_model=PrometheusInput,
            handler=prometheus_handler,
        ),
    ]
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_tool_registry.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~162 pass, 2 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/tools/registry.py tests/unit/test_tool_registry.py
git commit -m "Add all_tool_specs() factory for shared tool registration"
```

---

## Task 4: Refactor `agent.build_options` to consume the registry

**Files:**

- Modify: `src/k8sense/agent.py`
- Modify: `tests/unit/test_agent_helpers.py`

This task swaps the direct `kubectl_tool` / `prometheus_tool` imports for a registry-driven loop. The existing tests continue to pass with no changes to assertions.

- [ ] **Step 1: Confirm the existing tests still describe the contract**

Run:

```bash
.venv/bin/pytest tests/unit/test_agent_helpers.py -v
```

Expected: all pass (the contract being verified — both tool names in `allowed_tools`, three subagents in `agents` — is what we'll preserve).

- [ ] **Step 2: Update `build_options` in `src/k8sense/agent.py`**

Find this block at the top of `agent.py`:

```python
from k8sense.tools.kubectl import kubectl_tool
from k8sense.tools.prometheus import prometheus_tool
```

Replace with:

```python
from claude_agent_sdk import tool

from k8sense.tools.registry import all_tool_specs
```

(Remove the now-unused `kubectl_tool` and `prometheus_tool` direct imports. The `tool` decorator from `claude_agent_sdk` was previously imported transitively via the tools modules; we now use it directly.)

Find the existing `build_options` function and replace its body with:

```python
def build_options(system_prompt: str, model_id: str = DEFAULT_MODEL) -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions via the shared tool registry."""
    sdk_tools = [
        tool(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_model.model_json_schema(),
        )(spec.handler)
        for spec in all_tool_specs()
    ]
    server = create_sdk_mcp_server(name="k8sense", version="0.3.0", tools=sdk_tools)
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"k8sense": server},
        allowed_tools=[f"mcp__k8sense__{spec.name}" for spec in all_tool_specs()],
        agents={
            "event_triager": event_triager_definition,
            "log_investigator": log_investigator_definition,
            "metrics_analyst": metrics_analyst_definition,
        },
        model=model_id,
    )
```

- [ ] **Step 3: Run the agent_helpers tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_agent_helpers.py -v
```

Expected: all tests still pass — the behaviour is preserved.

- [ ] **Step 4: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~162 pass, 2 skipped (same count — refactor only).

- [ ] **Step 5: Confirm `k8sense ask` still works end-to-end**

Run:

```bash
.venv/bin/k8sense ask "list every namespace, briefly" 2>&1 | tail -10
```

Expected: produces a coherent answer with at least one namespace mentioned. (No new behaviour; just verifies the refactor didn't break the live path.)

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/agent.py
git commit -m "Refactor build_options to consume tools/registry.py"
```

---

## Task 5: MCP resources — content helpers + DNS-1123 validator (TDD)

**Files:**

- Create: `src/k8sense/mcp_server/__init__.py`
- Create: `src/k8sense/mcp_server/resources.py`
- Create: `tests/unit/test_mcp_resources.py`

- [ ] **Step 1: Write the failing tests for the content helpers**

Create `tests/unit/test_mcp_resources.py`:

````python
"""MCP resources — content helpers, namespace validation, URI dispatch."""
import pytest

from k8sense.mcp_server.resources import (
    _is_valid_namespace,
    _manifests_content,
    _recent_events_content,
    _topology_content,
)


@pytest.mark.parametrize("ns", ["argocd", "kube-system", "longhorn-system", "a", "abc-123"])
def test_namespace_validation_accepts_dns1123(ns):
    assert _is_valid_namespace(ns) is True


@pytest.mark.parametrize("ns", [
    "",          # empty
    "Argocd",    # uppercase
    "argo_cd",   # underscore
    "--all",     # leading dashes
    "ns space",  # space
    "../etc",    # path traversal
    "a" * 64,    # over 63 chars
])
def test_namespace_validation_rejects_invalid(ns):
    assert _is_valid_namespace(ns) is False


@pytest.mark.asyncio
async def test_topology_content_succeeds_with_real_cluster():
    # Real cluster reachable in this dev environment. The body contains the markdown
    # heading and a fenced code block.
    body = await _topology_content()
    assert body.startswith("# Cluster topology")
    assert "```" in body


@pytest.mark.asyncio
async def test_topology_content_returns_error_body_when_kubectl_missing(monkeypatch, tmp_path):
    # PATH manipulation hides kubectl; the helper returns a markdown error body, not an exception.
    monkeypatch.setenv("PATH", str(tmp_path))
    body = await _topology_content()
    assert body.startswith("# Topology fetch failed")
    assert "stderr" in body


@pytest.mark.asyncio
async def test_manifests_content_rejects_invalid_namespace_without_calling_kubectl():
    body = await _manifests_content("../etc")
    assert body.startswith("# Invalid namespace")
    assert "DNS-1123" in body


@pytest.mark.asyncio
async def test_manifests_content_succeeds_for_valid_namespace():
    body = await _manifests_content("kube-system")
    # The kube-system namespace exists and `get all` returns a YAML block.
    assert body.startswith("# Manifests in `kube-system`")


@pytest.mark.asyncio
async def test_recent_events_content_returns_markdown_with_code_fence():
    body = await _recent_events_content()
    # Whether or not there are events, the body is a markdown doc.
    assert body.startswith("# Recent Warning events")
    assert "```" in body


@pytest.mark.asyncio
async def test_recent_events_content_caps_at_30_lines():
    body = await _recent_events_content()
    # Count non-fence lines inside the body; the cap should keep it small.
    in_fence = False
    payload_line_count = 0
    for line in body.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            payload_line_count += 1
    # The actual cap is 30; allow some margin since the kubectl output may
    # contain header lines that count.
    assert payload_line_count <= 32, f"expected ≤32 lines inside fence, got {payload_line_count}"
````

- [ ] **Step 2: Run the tests — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_mcp_resources.py -v
```

Expected: `ImportError: No module named 'k8sense.mcp_server'`.

- [ ] **Step 3: Create the package init**

Create `src/k8sense/mcp_server/__init__.py`:

```python
"""k8sense MCP server — exposes tools, resources, and prompts over stdio."""
```

- [ ] **Step 4: Implement the resources module**

Create `src/k8sense/mcp_server/resources.py`:

````python
"""MCP resources: live topology, namespace manifests, recent Warning events."""
from __future__ import annotations

import re

from k8sense.tools.kubectl import run_kubectl

# DNS-1123 label: lowercase alphanumeric and hyphens, 1-63 chars.
_NAMESPACE_RE = re.compile(r"^[a-z0-9-]{1,63}$")
_EVENT_LINES_CAP = 30


def _is_valid_namespace(ns: str) -> bool:
    return bool(_NAMESPACE_RE.match(ns))


async def _topology_content() -> str:
    result = await run_kubectl(["get", "ns,nodes", "-o", "wide"])
    if result["exit_code"] != 0:
        return f"# Topology fetch failed\n\nstderr: {result['stderr']}"
    return f"# Cluster topology\n\n```\n{result['stdout']}\n```\n"


async def _manifests_content(namespace: str) -> str:
    if not _is_valid_namespace(namespace):
        return (
            f"# Invalid namespace\n\n"
            f"Namespace '{namespace}' is not a valid DNS-1123 label."
        )
    result = await run_kubectl(["get", "all", "-n", namespace, "-o", "yaml"])
    if result["exit_code"] != 0:
        return f"# Manifests fetch failed for {namespace}\n\nstderr: {result['stderr']}"
    return f"# Manifests in `{namespace}`\n\n```yaml\n{result['stdout']}\n```\n"


async def _recent_events_content() -> str:
    result = await run_kubectl([
        "get", "events", "-A",
        "--field-selector=type=Warning",
        "--sort-by=.lastTimestamp",
    ])
    if result["exit_code"] != 0:
        return f"# Recent events fetch failed\n\nstderr: {result['stderr']}"
    lines = result["stdout"].splitlines()
    body = "\n".join(lines[-_EVENT_LINES_CAP:]) if len(lines) > _EVENT_LINES_CAP else result["stdout"]
    return f"# Recent Warning events\n\n```\n{body}\n```\n"
````

(The `register_resources(server)` function comes in Task 6, once we have the Server import sorted.)

- [ ] **Step 5: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_mcp_resources.py -v
```

Expected: all tests pass. The cluster-touching ones run because kubectl + cluster are reachable in this dev environment.

- [ ] **Step 6: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~178 pass (~162 prior + ~16 new), 2 skipped.

- [ ] **Step 7: Commit**

```bash
git add src/k8sense/mcp_server/__init__.py src/k8sense/mcp_server/resources.py tests/unit/test_mcp_resources.py
git commit -m "Add MCP resource content helpers with DNS-1123 validation"
```

---

## Task 6: MCP resources — `register_resources(server)` (TDD)

**Files:**

- Modify: `src/k8sense/mcp_server/resources.py`
- Modify: `tests/unit/test_mcp_resources.py`

- [ ] **Step 1: Append failing tests for the registration helper**

Append to `tests/unit/test_mcp_resources.py`:

```python


from mcp.server.lowlevel.server import Server  # noqa: E402

from k8sense.mcp_server.resources import register_resources  # noqa: E402


def _build_server_with_resources() -> Server:
    server = Server("k8sense-test")
    register_resources(server)
    return server


def test_register_resources_attaches_list_resources_handler():
    server = _build_server_with_resources()
    # mcp.server.Server stores handlers in `request_handlers` keyed by request type.
    from mcp import types
    assert types.ListResourcesRequest in server.request_handlers
    assert types.ListResourceTemplatesRequest in server.request_handlers
    assert types.ReadResourceRequest in server.request_handlers
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_mcp_resources.py::test_register_resources_attaches_list_resources_handler -v
```

Expected: `ImportError: cannot import name 'register_resources' from 'k8sense.mcp_server.resources'`.

- [ ] **Step 3: Append `register_resources` to `src/k8sense/mcp_server/resources.py`**

Append at the bottom:

```python


from mcp.server.lowlevel.server import Server  # noqa: E402
from mcp.types import Resource, ResourceTemplate  # noqa: E402
from pydantic import AnyUrl  # noqa: E402


def register_resources(server: Server) -> None:
    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri=AnyUrl("mcp://k8sense/topology"),
                name="Cluster topology",
                description="Current namespaces and nodes (kubectl get ns,nodes -o wide).",
                mimeType="text/markdown",
            ),
            Resource(
                uri=AnyUrl("mcp://k8sense/events/recent"),
                name="Recent Warning events",
                description=f"Last {_EVENT_LINES_CAP} cluster-wide Warning events.",
                mimeType="text/markdown",
            ),
        ]

    @server.list_resource_templates()
    async def list_resource_templates() -> list[ResourceTemplate]:
        return [
            ResourceTemplate(
                uriTemplate="mcp://k8sense/manifests/{namespace}",
                name="Namespace manifests",
                description="kubectl get all -n <namespace> -o yaml. Replace {namespace}.",
                mimeType="text/markdown",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> str:
        uri_str = str(uri)
        if uri_str == "mcp://k8sense/topology":
            return await _topology_content()
        if uri_str == "mcp://k8sense/events/recent":
            return await _recent_events_content()
        prefix = "mcp://k8sense/manifests/"
        if uri_str.startswith(prefix):
            ns = uri_str[len(prefix):]
            return await _manifests_content(ns)
        raise ValueError(f"unknown resource: {uri_str}")
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_mcp_resources.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: one new test passes; total ~179.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/mcp_server/resources.py tests/unit/test_mcp_resources.py
git commit -m "Add register_resources() to attach MCP resource handlers"
```

---

## Task 7: MCP prompts — content helpers + validation (TDD)

**Files:**

- Create: `src/k8sense/mcp_server/prompts.py`
- Create: `tests/unit/test_mcp_prompts.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mcp_prompts.py`:

```python
"""MCP prompts — template assembly, namespace validation, dispatch."""
import pytest

from k8sense.mcp_server.prompts import (
    _investigate_pod,
    _metrics,
    _triage_events,
    _validate_namespace,
)


def test_validate_namespace_accepts_valid():
    # Should not raise
    _validate_namespace("argocd")
    _validate_namespace("kube-system")


@pytest.mark.parametrize("bad", ["", "Argocd", "argo_cd", "--all", "../etc"])
def test_validate_namespace_rejects_invalid(bad):
    with pytest.raises(ValueError, match="invalid namespace"):
        _validate_namespace(bad)


def test_investigate_pod_includes_pod_and_namespace_in_describe():
    text = _investigate_pod(pod="argocd-server-7d", namespace="argocd")
    assert "argocd-server-7d" in text
    assert "argocd" in text
    assert "kubectl describe pod argocd-server-7d -n argocd" in text


def test_investigate_pod_mentions_common_patterns():
    text = _investigate_pod(pod="x", namespace="argocd")
    assert "OOMKilled" in text
    assert "ImagePullBackOff" in text
    assert "CrashLoopBackOff" in text
    assert "--previous" in text


def test_investigate_pod_rejects_bad_namespace():
    with pytest.raises(ValueError):
        _investigate_pod(pod="x", namespace="../etc")


def test_triage_events_cluster_wide_when_no_namespace():
    text = _triage_events(namespace=None)
    assert "cluster (all namespaces)" in text
    assert "-A" in text


def test_triage_events_scoped_to_namespace():
    text = _triage_events(namespace="argocd")
    assert "argocd" in text
    assert "-n argocd" in text


def test_triage_events_rejects_bad_namespace():
    with pytest.raises(ValueError):
        _triage_events(namespace="--all")


def test_metrics_snapshot_when_no_lookback():
    text = _metrics(namespace="monitoring", lookback=None)
    assert "kubectl top pods -n monitoring" in text


def test_metrics_trend_when_lookback_provided():
    text = _metrics(namespace="monitoring", lookback="1h")
    assert "container_cpu_usage_seconds_total" in text
    assert "1h" in text


def test_metrics_rejects_bad_namespace():
    with pytest.raises(ValueError):
        _metrics(namespace="ns space", lookback=None)
```

- [ ] **Step 2: Run the tests — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_mcp_prompts.py -v
```

Expected: `ImportError: No module named 'k8sense.mcp_server.prompts'`.

- [ ] **Step 3: Implement the prompts module**

Create `src/k8sense/mcp_server/prompts.py`:

```python
"""MCP prompts — three workflow templates mirroring Phase 2 subagent playbooks."""
from __future__ import annotations

import re

_NAMESPACE_RE = re.compile(r"^[a-z0-9-]{1,63}$")


def _validate_namespace(ns: str) -> None:
    if not _NAMESPACE_RE.match(ns):
        raise ValueError(f"invalid namespace: {ns!r}")


def _investigate_pod(pod: str, namespace: str) -> str:
    _validate_namespace(namespace)
    return (
        f"Investigate pod `{pod}` in namespace `{namespace}` on the homelab-k3s cluster.\n\n"
        "Follow this playbook:\n"
        f"1. Read events and restart history: `kubectl describe pod {pod} -n {namespace}`\n"
        f"2. Tail current logs: `kubectl logs {pod} -n {namespace} --tail=200`\n"
        "3. If logs are empty, retry with `--previous` to see the prior container's logs.\n"
        "4. Recognise common patterns from describe output:\n"
        "   - `OOMKilled` → memory limit hit.\n"
        "   - `ImagePullBackOff` → image / registry / credentials issue.\n"
        "   - `CrashLoopBackOff` with empty current logs → check `--previous`.\n"
        "5. Quote 2-3 concrete log lines in your final answer instead of paraphrasing.\n\n"
        "Keep the final answer to one short paragraph plus the quoted log lines."
    )


def _triage_events(namespace: str | None) -> str:
    if namespace is not None:
        _validate_namespace(namespace)
    scope = f"the `{namespace}` namespace" if namespace else "the cluster (all namespaces)"
    selector = f"-n {namespace}" if namespace else "-A"
    return (
        f"Triage recent Kubernetes events in {scope} on the homelab-k3s cluster.\n\n"
        "Follow this playbook:\n"
        f"1. List recent Warning events: "
        f"`kubectl get events {selector} --sort-by=.lastTimestamp --field-selector=type=Warning`\n"
        "2. Summarise the top 5 by recency. For each include:\n"
        "   reason, count, firstTimestamp, lastTimestamp, and object kind/name.\n"
        "3. If no Warning events were found, say so explicitly — do not fabricate concern.\n\n"
        "Final answer should be a short bullet list."
    )


def _metrics(namespace: str, lookback: str | None) -> str:
    _validate_namespace(namespace)
    if lookback is None:
        return (
            f"Report current resource usage for namespace `{namespace}` on the homelab-k3s cluster.\n\n"
            f"Run: `kubectl top pods -n {namespace}`\n"
            "Summarise which pods are using the most CPU and memory. Quote concrete numbers."
        )
    return (
        f"Report resource usage trends for namespace `{namespace}` "
        f"over the last `{lookback}` on the homelab-k3s cluster.\n\n"
        "Use prometheus_query with these PromQL primitives:\n"
        f'- pod CPU rate: `sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[2m])) by (pod)`\n'
        f'- pod memory: `container_memory_working_set_bytes{{namespace="{namespace}"}}`\n\n'
        f"Pass `lookback={lookback}` for the range query. Summarise concrete numbers."
    )
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_mcp_prompts.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~196 pass, 2 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/mcp_server/prompts.py tests/unit/test_mcp_prompts.py
git commit -m "Add MCP prompt content helpers with namespace validation"
```

---

## Task 8: MCP prompts — `register_prompts(server)` (TDD)

**Files:**

- Modify: `src/k8sense/mcp_server/prompts.py`
- Modify: `tests/unit/test_mcp_prompts.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/unit/test_mcp_prompts.py`:

```python


from mcp.server.lowlevel.server import Server  # noqa: E402

from k8sense.mcp_server.prompts import register_prompts  # noqa: E402


def _build_server_with_prompts() -> Server:
    server = Server("k8sense-test")
    register_prompts(server)
    return server


def test_register_prompts_attaches_list_and_get_prompt_handlers():
    server = _build_server_with_prompts()
    from mcp import types
    assert types.ListPromptsRequest in server.request_handlers
    assert types.GetPromptRequest in server.request_handlers
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_mcp_prompts.py -v
```

Expected: `ImportError: cannot import name 'register_prompts' from 'k8sense.mcp_server.prompts'`.

- [ ] **Step 3: Append `register_prompts` to `src/k8sense/mcp_server/prompts.py`**

Append at the bottom:

```python


from mcp.server.lowlevel.server import Server  # noqa: E402
from mcp.types import (  # noqa: E402
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
)


def register_prompts(server: Server) -> None:
    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return [
            Prompt(
                name="investigate-pod",
                description="Investigate why a specific pod is failing or restarting.",
                arguments=[
                    PromptArgument(name="pod", description="Pod name", required=True),
                    PromptArgument(name="namespace", description="Namespace the pod is in", required=True),
                ],
            ),
            Prompt(
                name="triage-events",
                description="Scan recent Warning events; optionally narrow to a namespace.",
                arguments=[
                    PromptArgument(
                        name="namespace",
                        description="Namespace to scope to (omit for cluster-wide)",
                        required=False,
                    ),
                ],
            ),
            Prompt(
                name="metrics",
                description="Report resource usage for a namespace (snapshot or trend).",
                arguments=[
                    PromptArgument(name="namespace", description="Namespace to inspect", required=True),
                    PromptArgument(
                        name="lookback",
                        description="Trend window like '1h' / '24h'; omit for snapshot",
                        required=False,
                    ),
                ],
            ),
        ]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        args = arguments or {}
        if name == "investigate-pod":
            text = _investigate_pod(pod=args["pod"], namespace=args["namespace"])
        elif name == "triage-events":
            text = _triage_events(namespace=args.get("namespace"))
        elif name == "metrics":
            text = _metrics(namespace=args["namespace"], lookback=args.get("lookback"))
        else:
            raise ValueError(f"unknown prompt: {name}")
        return GetPromptResult(
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))]
        )
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_mcp_prompts.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~197 pass.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/mcp_server/prompts.py tests/unit/test_mcp_prompts.py
git commit -m "Add register_prompts() to attach MCP prompt handlers"
```

---

## Task 9: MCP server assembly — `build_server()` + `run_stdio()` (TDD)

**Files:**

- Create: `src/k8sense/mcp_server/server.py`
- Create: `tests/unit/test_mcp_server.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mcp_server.py`:

```python
"""MCP server assembly — build_server + tool registration via registry."""
import pytest

from k8sense.mcp_server.server import build_server


def test_build_server_returns_a_server_instance():
    from mcp.server.lowlevel.server import Server
    server = build_server()
    assert isinstance(server, Server)


def test_build_server_registers_all_required_handlers():
    from mcp import types
    server = build_server()
    expected = [
        types.ListToolsRequest,
        types.CallToolRequest,
        types.ListResourcesRequest,
        types.ListResourceTemplatesRequest,
        types.ReadResourceRequest,
        types.ListPromptsRequest,
        types.GetPromptRequest,
    ]
    for handler_type in expected:
        assert handler_type in server.request_handlers, f"missing handler for {handler_type.__name__}"


@pytest.mark.asyncio
async def test_call_tool_dispatches_kubectl_via_registry():
    """A call_tool request for `kubectl` with a disallowed verb should reach the
    real handler and come back as a content-block list with the rejection text."""
    server = build_server()
    from mcp import types
    # Find the registered call_tool handler
    handler = server.request_handlers[types.CallToolRequest]
    # Build a minimal request envelope
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="kubectl",
            arguments={"args": ["delete", "pod", "x"]},
        ),
    )
    result = await handler(req)
    # The high-level Server wraps the content in a ServerResult; pull out the content list
    payload = result.root.content if hasattr(result, "root") else result.content
    assert any("not allowed" in block.text for block in payload if hasattr(block, "text"))
```

- [ ] **Step 2: Run the tests — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_mcp_server.py -v
```

Expected: `ImportError: No module named 'k8sense.mcp_server.server'`.

- [ ] **Step 3: Implement `build_server()` and `run_stdio()`**

Create `src/k8sense/mcp_server/server.py`:

```python
"""Build and run the k8sense MCP server."""
from __future__ import annotations

from typing import Any

from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from k8sense.mcp_server.prompts import register_prompts
from k8sense.mcp_server.resources import register_resources
from k8sense.tools.registry import all_tool_specs


def build_server() -> Server:
    """Construct a configured MCP Server with all k8sense tools, resources, and prompts."""
    server = Server("k8sense")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.input_model.model_json_schema(),
            )
            for spec in all_tool_specs()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        for spec in all_tool_specs():
            if spec.name == name:
                validated = spec.input_model(**arguments).model_dump(exclude_none=True)
                result = await spec.handler(validated)
                # The handler returns {"content": [{"type": "text", "text": ...}]}.
                # Convert each block to a TextContent for the MCP layer.
                return [
                    TextContent(type="text", text=block["text"])
                    for block in result["content"]
                    if block.get("type") == "text"
                ]
        raise ValueError(f"unknown tool: {name}")

    register_resources(server)
    register_prompts(server)
    return server


async def run_stdio() -> None:
    """Run the MCP server over stdio. Returns when the parent closes the pipe."""
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_mcp_server.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~200 pass, 2 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/mcp_server/server.py tests/unit/test_mcp_server.py
git commit -m "Add build_server() and run_stdio() for the k8sense MCP server"
```

---

## Task 10: CLI `mcp` subcommand

**Files:**

- Modify: `src/k8sense/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test for the new subcommand**

Append to `tests/unit/test_cli.py`:

```python


def test_parser_accepts_mcp_subcommand():
    parser = build_parser()
    ns = parser.parse_args(["mcp"])
    assert ns.command == "mcp"
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_cli.py::test_parser_accepts_mcp_subcommand -v
```

Expected: FAIL — argparse rejects `mcp` as an unknown subcommand.

- [ ] **Step 3: Add the subcommand to `build_parser` and `main`**

In `src/k8sense/cli.py`, find the existing subparsers block in `build_parser`:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="k8sense", description="Homelab k3s SRE agent")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="ask a question about the cluster")
    ask.add_argument("question", help="natural-language question, e.g. 'why is pod X crashing?'")

    sub.add_parser("doctor", help="check the local environment")

    return parser
```

Add a new subparser line right before `return parser`:

```python
    sub.add_parser("mcp", help="run k8sense as a stdio MCP server")

    return parser
```

In the same file, find the `main` function. Add a new branch alongside the existing `doctor` and `ask` handlers (before any "unreachable" guard):

```python
    if ns.command == "mcp":
        from k8sense.mcp_server.server import run_stdio
        try:
            asyncio.run(run_stdio())
        except KeyboardInterrupt:
            return 130
        return 0
```

- [ ] **Step 4: Run the test — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_cli.py -v
```

Expected: all CLI tests pass, including the new one.

- [ ] **Step 5: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~201 pass.

- [ ] **Step 6: Sanity-check the subcommand exists**

Run:

```bash
.venv/bin/k8sense --help 2>&1 | grep -A 3 "Commands\|positional"
```

Expected: output mentions `mcp` as one of the subcommands. (argparse prints subcommand help differently across versions; the exact format isn't important.)

- [ ] **Step 7: Commit**

```bash
git add src/k8sense/cli.py tests/unit/test_cli.py
git commit -m "Add k8sense mcp subcommand to run the stdio MCP server"
```

---

## Task 11: Stdio smoke test — end-to-end JSON-RPC handshake

**Files:**

- Create: `tests/smoke/test_mcp_stdio.py`

- [ ] **Step 1: Write the smoke test**

Create `tests/smoke/test_mcp_stdio.py`:

```python
"""End-to-end smoke: spawn `k8sense mcp` as a subprocess, complete the MCP
initialization handshake, then send tools/list, resources/list, and prompts/list
and assert the responses contain the expected names.

Run with:
    K8SENSE_ALLOW_API=1 pytest -m smoke -s tests/smoke/test_mcp_stdio.py
"""
import asyncio
import json
import os

import pytest


async def _request(proc: asyncio.subprocess.Process, body: dict, request_id: int) -> dict:
    """Send a JSON-RPC request over stdin, read one line of response from stdout."""
    payload = {"jsonrpc": "2.0", "id": request_id, **body}
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    await proc.stdin.drain()
    line = await proc.stdout.readline()
    return json.loads(line.decode())


async def _notify(proc: asyncio.subprocess.Process, body: dict) -> None:
    """Send a JSON-RPC notification (no response expected)."""
    payload = {"jsonrpc": "2.0", **body}
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    await proc.stdin.drain()


async def _run_mcp_session() -> dict:
    proc = await asyncio.create_subprocess_exec(
        ".venv/bin/k8sense", "mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        # 1. initialize
        init_resp = await _request(proc, {
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "k8sense-smoke", "version": "0"},
            },
        }, 1)
        # 2. notifications/initialized — required handshake completion
        await _notify(proc, {"method": "notifications/initialized"})

        # 3. tools/list
        tools_resp = await _request(proc, {"method": "tools/list"}, 2)
        # 4. resources/list
        resources_resp = await _request(proc, {"method": "resources/list"}, 3)
        # 5. prompts/list
        prompts_resp = await _request(proc, {"method": "prompts/list"}, 4)

        return {
            "init": init_resp,
            "tools": tools_resp,
            "resources": resources_resp,
            "prompts": prompts_resp,
        }
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            proc.terminate()
            await proc.wait()


@pytest.mark.smoke
def test_mcp_stdio_handshake_and_lists():
    if not os.environ.get("K8SENSE_ALLOW_API"):
        pytest.skip("smoke test requires K8SENSE_ALLOW_API=1")

    result = asyncio.run(_run_mcp_session())

    # Initialize must succeed
    assert "result" in result["init"], f"init failed: {result['init']}"
    assert result["init"]["result"]["serverInfo"]["name"] == "k8sense"

    # tools/list returns kubectl and prometheus_query
    tool_names = {t["name"] for t in result["tools"]["result"]["tools"]}
    assert tool_names == {"kubectl", "prometheus_query"}, f"unexpected tools: {tool_names}"

    # resources/list returns topology and events/recent (the templated one is in resources/templates/list)
    resource_uris = {str(r["uri"]) for r in result["resources"]["result"]["resources"]}
    assert "mcp://k8sense/topology" in resource_uris
    assert "mcp://k8sense/events/recent" in resource_uris

    # prompts/list returns the three workflow prompts
    prompt_names = {p["name"] for p in result["prompts"]["result"]["prompts"]}
    assert prompt_names == {"investigate-pod", "triage-events", "metrics"}
```

- [ ] **Step 2: Verify the default suite skips it**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~201 passed + 3 skipped (the two prior smoke tests + this new one).

- [ ] **Step 3: Run the smoke test manually**

Run:

```bash
K8SENSE_ALLOW_API=1 .venv/bin/pytest -m smoke tests/smoke/test_mcp_stdio.py -v -s
```

Expected: the smoke test passes. The subprocess spawns, the handshake completes, and the three list responses contain the expected names. If this fails, the most likely cause is an MCP package version mismatch on the protocol version string ("2025-03-26") — check the `mcp.types.LATEST_PROTOCOL_VERSION` constant and use that instead.

- [ ] **Step 4: Commit**

```bash
git add tests/smoke/test_mcp_stdio.py
git commit -m "Add stdio smoke test: handshake, tools/resources/prompts list"
```

---

## Task 12: README polish + Phase 3 tag

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Update the README's phase table**

In `README.md`, find the phase ladder table near the top:

```markdown
| Phase | Tag                                                   | Capability                                             | Concepts introduced                                         |
| ----- | ----------------------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------- |
| **1** | [`phase-1`](#phase-1--one-shot-investigator)          | `k8sense ask` — single agent investigates with kubectl | Agent loop, custom tools, streaming, eval harness           |
| **2** | [`phase-2`](#phase-2--parallel-subagents--prometheus) | Parallel subagent dispatch + Prometheus tool           | `AgentDefinition`, `background=True`, multi-tool MCP server |
| 3-5   | —                                                     | MCP server, hooks/memory, sentinel daemon              | (See spec.)                                                 |
```

Replace the third row with two rows:

```markdown
| **3** | [`phase-3`](#phase-3--mcp-server) | `k8sense mcp` stdio server — Claude Code can attach | MCP `Server`, tools/resources/prompts, Pydantic JSON Schemas |
| 4-5 | — | Hooks/memory, sentinel daemon | (See spec.) |
```

- [ ] **Step 2: Add the Phase 3 section**

In `README.md`, find the end of the Phase 2 section (just before the "Run the eval suite" or "Run the test suite" heading). Insert this new section:

````markdown
---

## Phase 3 — MCP server

Phase 3 exposes k8sense as a **stdio MCP server**: the same tools, plus three live cluster resources and three workflow prompts, become available inside Claude Code (or any other MCP client). The existing `k8sense ask` flow stays in-process for speed; both transports share `tools/registry.py` so behaviour stays consistent.

### What gets exposed

| Kind      | Names                                                                                          |
| --------- | ---------------------------------------------------------------------------------------------- |
| Tools     | `kubectl`, `prometheus_query` (with Pydantic JSON Schemas)                                     |
| Resources | `mcp://k8sense/topology`, `mcp://k8sense/manifests/{namespace}`, `mcp://k8sense/events/recent` |
| Prompts   | `investigate-pod`, `triage-events`, `metrics`                                                  |

### Add to Claude Code

Add this entry to `~/.claude.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "k8sense": {
      "command": "/absolute/path/to/k8sense",
      "args": ["mcp"]
    }
  }
}
```

(Find the path with `which k8sense` after activating the venv.)

Once Claude Code reconnects, you can:

- Read the resources from the resource picker (e.g. `mcp://k8sense/topology` to inject the cluster topology into context).
- Call the tools directly from any Claude Code session.
- Invoke the slash commands `/k8sense:investigate-pod`, `/k8sense:triage-events`, `/k8sense:metrics` with parameter prompts.

### Why both transports

`k8sense ask` keeps the in-process SDK path (no subprocess startup; tools called directly). `k8sense mcp` runs the same handlers over stdio for external consumption. The shared `tools/registry.py` means adding a new tool registers it on both paths in one place.
````

- [ ] **Step 3: Update the Architecture tree at the bottom**

Find the existing architecture tree and add the new entries:

```
src/k8sense/
├── ...
├── tools/
│   ├── kubectl.py
│   ├── prometheus.py
│   └── registry.py              # Phase 3: shared ToolSpec + Pydantic input models
└── mcp_server/                  # Phase 3: stdio MCP server
    ├── server.py                #   build_server() + run_stdio()
    ├── resources.py             #   3 live cluster resources
    └── prompts.py               #   3 workflow prompts
```

(Edit the existing tree in place — don't append a duplicate.)

- [ ] **Step 4: Run the full test suite one more time**

Run:

```bash
.venv/bin/pytest -v
```

Expected: all unit tests pass + 3 smoke skipped.

- [ ] **Step 5: Commit README**

```bash
git add README.md
git commit -m "Phase 3 complete: ship k8sense mcp stdio server + docs"
```

- [ ] **Step 6: Tag and push**

```bash
git tag -a phase-3 -m "Phase 3: stdio MCP server with tools, resources, prompts"
git push origin main
git push origin phase-3
git log --oneline -5
```

Expected: `phase-3` tag exists locally and on GitHub, pointing at the latest commit.

---

## Phase 3 acceptance checklist

After all tasks are complete, verify:

- [ ] `k8sense ask "list every namespace"` still works (registry refactor didn't regress the in-process path).
- [ ] `pytest` passes (all 144+ unit tests + ~55 new Phase 3 unit tests).
- [ ] `K8SENSE_ALLOW_API=1 pytest -m smoke` runs all three smoke tests against the real cluster.
- [ ] `.venv/bin/k8sense mcp` runs without error when stdin is closed by the parent.
- [ ] The smoke test confirms the MCP handshake works end-to-end and the three primitives are exposed.
- [ ] After adding the Claude Code config block and reconnecting, you can invoke `/k8sense:investigate-pod` and see the parameter UI.
- [ ] No mocking of kubectl, Prometheus, the SDK, or the MCP server anywhere in the test suite.

If all boxes are ticked, Phase 3 is shippable and we can move to Phase 4 (hooks, memory, permission modes).
