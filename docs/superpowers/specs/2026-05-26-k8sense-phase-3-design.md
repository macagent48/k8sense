# k8sense Phase 3 — Design Spec (MCP Server)

**Date:** 2026-05-26
**Author:** Yash
**Status:** Draft, pending review
**Parent spec:** `docs/superpowers/specs/2026-05-25-k8sense-design.md`
**Predecessor:** Phase 2 at git tag `phase-2` (commit `0fdf82a`)

## Goal

Expose k8sense as a stdio MCP server (`k8sense mcp`) so the same kubectl + Prometheus tools, plus three live cluster resources and three workflow prompts, become available inside Claude Code (and any other MCP client). The existing `k8sense ask` flow stays in-process for speed; both transports share one tool registry so behaviour stays consistent.

This is the curriculum's first lesson in MCP server authorship: tools, resources, and prompts on a single server, with proper Pydantic-derived JSON Schemas at the tool input boundary.

## Non-goals

- HTTP/SSE transports. Stdio only. HTTP MCP servers belong to Phase 4+/5 when the sentinel might be queried remotely.
- MCP authorisation. The stdio server runs as the same user that invoked it; local kubectl auth applies. No bearer tokens.
- A `loki_query` tool. Still deferred (Phase 1 non-goals list).
- Caching of resource bodies. Live fetch on every read.
- Exposing the Phase 2 subagents through MCP. Prompts in this phase are templates that mirror subagent playbooks, not the subagents themselves — MCP has no standard "dispatch this subagent" primitive today.
- Refactoring `k8sense ask` to use the stdio server. It stays in-process to keep per-invocation latency tight.
- New eval entries by default. The existing 15-question dataset still passes through the in-process SDK path. (Optional: 2-3 stdio-flow evals as a Phase 3.1 add-on if useful.)

## Decisions locked in during brainstorming

- **Both transports.** In-process SDK tools for `k8sense ask` AND a stdio MCP server for external consumption. Both pull from one `tools/registry.py`. Smallest blast radius.
- **Full MCP feature surface.** Tools + resources + prompts — maximises learning value.
- **Three live resources:** `mcp://k8sense/topology`, `mcp://k8sense/manifests/{namespace}`, `mcp://k8sense/events/recent`.
- **Three workflow prompts** mirroring Phase 2 subagents: `investigate-pod(pod, namespace)`, `triage-events(namespace?)`, `metrics(namespace, lookback?)`.
- **Pydantic input models for tools** (`KubectlInput`, `PrometheusInput`). The Pydantic-generated JSON Schema replaces today's bare dict in the SDK tool registration too — both transports gain better parameter hints.
- **Namespace validation via DNS-1123 regex** before any kubectl invocation that takes a namespace as a parameter (resources and prompts both).
- **Events resource is bounded by count** (last 30), not by time window.

## Architecture

The Phase 2 agent loop is unchanged. Phase 3 adds:

1. A `tools/registry.py` module — one source of truth for the two existing tools, exposing them as `ToolSpec` dataclasses with Pydantic input models.
2. An `mcp_server/` package — builds a `mcp.server.Server` that exposes tools, resources, and prompts; the `k8sense mcp` CLI subcommand runs it over stdio.
3. Both transports consume the same `tools/registry.py`. `build_options()` in `agent.py` and `mcp_server/server.py` each iterate `all_tool_specs()` and wrap them in their respective registration shells.

```
                  ┌──────────────────────────────────────┐
                  │  tools/registry.py                   │
                  │   - KubectlInput (Pydantic)          │
                  │   - PrometheusInput (Pydantic)       │
                  │   - all_tool_specs() → [ToolSpec]    │
                  └────────────────┬─────────────────────┘
                                   │ both transports import this
                ┌──────────────────┴──────────────────┐
                ▼                                     ▼
   in-process (k8sense ask)               stdio MCP (k8sense mcp)
   ──────────────────────────             ───────────────────────────
   agent.py: build_options()              mcp_server/server.py
     for spec in all_tool_specs():          @server.list_tools / call_tool
       tool(name=..., schema=...)(spec.handler)  iterates all_tool_specs()
     create_sdk_mcp_server(tools=[...])      + resources.py (3 resources)
   → ClaudeAgentOptions.mcp_servers          + prompts.py (3 prompts)
                                             server.run() over stdio pipes
```

Claude Code consumes the stdio path via a single config entry in `~/.claude.json`:

```json
{
  "mcpServers": {
    "k8sense": {
      "command": "/path/to/k8sense",
      "args": ["mcp"]
    }
  }
}
```

## Repo layout (post-Phase 3)

```
src/k8sense/
├── cli.py                       # +1 subcommand: `mcp`
├── agent.py                     # build_options() iterates registry.all_tool_specs()
├── tools/
│   ├── kubectl.py               # unchanged (handler stays as-is)
│   ├── prometheus.py            # unchanged (handler stays as-is)
│   └── registry.py              # NEW (~80 lines): ToolSpec + Pydantic models + factory
├── mcp_server/                  # NEW directory
│   ├── __init__.py
│   ├── server.py                # ~60 lines: assemble Server, expose run_stdio()
│   ├── resources.py             # ~120 lines: 3 resources + DNS-1123 validation
│   └── prompts.py               # ~100 lines: 3 prompts + dispatch
├── subagents/                   # unchanged
├── prompts/system.py            # unchanged
└── render.py                    # unchanged

tests/unit/
├── test_tool_registry.py        # NEW: registry + Pydantic validation
├── test_mcp_resources.py        # NEW: resource handlers + URI dispatch + size cap
├── test_mcp_prompts.py          # NEW: prompt templates + dispatch
└── test_mcp_server.py           # NEW: tool registration through Server, dispatch
tests/smoke/
└── test_mcp_stdio.py            # NEW: spawn k8sense mcp, send tools/list, assert
```

---

## Component design

### 1. `tools/registry.py` — one source of truth

```python
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from k8sense.tools.kubectl import kubectl_handler
from k8sense.tools.prometheus import prometheus_handler


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

Tests pin the schema shape: `KubectlInput(args=[]).model_validate(...)` rejects empty list; `PrometheusInput(query="up", lookback="5xyz")` raises ValidationError on the regex.

### 2. `mcp_server/server.py` — the canonical server assembly

```python
from mcp.server import Server
from mcp.server.stdio import stdio_server

from k8sense.tools.registry import all_tool_specs
from k8sense.mcp_server.resources import register_resources
from k8sense.mcp_server.prompts import register_prompts


def build_server() -> Server:
    server = Server("k8sense")

    @server.list_tools()
    async def list_tools():
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": spec.input_model.model_json_schema(),
            }
            for spec in all_tool_specs()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        for spec in all_tool_specs():
            if spec.name == name:
                validated = spec.input_model(**arguments).model_dump(exclude_none=True)
                result = await spec.handler(validated)
                return result["content"]  # MCP expects content blocks directly
        raise ValueError(f"unknown tool: {name}")

    register_resources(server)
    register_prompts(server)
    return server


async def run_stdio() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
```

### 3. `mcp_server/resources.py` — three live cluster resources

URI scheme:

- `mcp://k8sense/topology` — `kubectl get ns,nodes -o wide`
- `mcp://k8sense/manifests/{namespace}` — `kubectl get all -n {namespace} -o yaml` (URI template)
- `mcp://k8sense/events/recent` — last 30 cluster-wide Warning events

Implementation:

````python
import re

from mcp.server import Server

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


def register_resources(server: Server) -> None:
    @server.list_resources()
    async def list_resources():
        return [
            {
                "uri": "mcp://k8sense/topology",
                "name": "Cluster topology",
                "description": "Current namespaces and nodes (kubectl get ns,nodes -o wide).",
                "mimeType": "text/markdown",
            },
            {
                "uri": "mcp://k8sense/events/recent",
                "name": "Recent Warning events",
                "description": f"Last {_EVENT_LINES_CAP} cluster-wide Warning events.",
                "mimeType": "text/markdown",
            },
        ]

    @server.list_resource_templates()
    async def list_resource_templates():
        return [
            {
                "uriTemplate": "mcp://k8sense/manifests/{namespace}",
                "name": "Namespace manifests",
                "description": "kubectl get all -n <namespace> -o yaml. Replace {namespace}.",
                "mimeType": "text/markdown",
            },
        ]

    @server.read_resource()
    async def read_resource(uri: str):
        if uri == "mcp://k8sense/topology":
            return [{"uri": uri, "mimeType": "text/markdown", "text": await _topology_content()}]
        if uri == "mcp://k8sense/events/recent":
            return [{"uri": uri, "mimeType": "text/markdown", "text": await _recent_events_content()}]
        if uri.startswith("mcp://k8sense/manifests/"):
            ns = uri[len("mcp://k8sense/manifests/"):]
            return [{"uri": uri, "mimeType": "text/markdown", "text": await _manifests_content(ns)}]
        raise ValueError(f"unknown resource: {uri}")
````

Key design choices:

- **Live, not cached.** Every read goes to the cluster.
- **Resources use `run_kubectl`** (the raw async function), not `kubectl_handler` — we want the unwrapped result so we can build markdown ourselves.
- **30-line cap** on events keeps the resource bounded regardless of cluster noise.
- **DNS-1123 validation** prevents passing `--all-namespaces`, `..`, etc. as a namespace.
- **Errors return markdown, not exceptions.** A failing fetch produces a readable resource body; the MCP read still succeeds.

### 4. `mcp_server/prompts.py` — three workflow prompts

Slash commands surface in Claude Code as `/k8sense:investigate-pod`, `/k8sense:triage-events`, `/k8sense:metrics`. Each prompt assembles a fully-formed message string from its parameters.

```python
import re

from mcp.server import Server

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
        f"3. If logs are empty, retry with `--previous` to see the prior container's logs.\n"
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


def register_prompts(server: Server) -> None:
    @server.list_prompts()
    async def list_prompts():
        return [
            {
                "name": "investigate-pod",
                "description": "Investigate why a specific pod is failing or restarting.",
                "arguments": [
                    {"name": "pod", "description": "Pod name", "required": True},
                    {"name": "namespace", "description": "Namespace the pod is in", "required": True},
                ],
            },
            {
                "name": "triage-events",
                "description": "Scan recent Warning events; optionally narrow to a namespace.",
                "arguments": [
                    {"name": "namespace", "description": "Namespace to scope to (omit for cluster-wide)", "required": False},
                ],
            },
            {
                "name": "metrics",
                "description": "Report resource usage for a namespace (snapshot or trend).",
                "arguments": [
                    {"name": "namespace", "description": "Namespace to inspect", "required": True},
                    {"name": "lookback", "description": "Trend window like '1h' / '24h'; omit for snapshot", "required": False},
                ],
            },
        ]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None):
        args = arguments or {}
        if name == "investigate-pod":
            text = _investigate_pod(args["pod"], args["namespace"])
        elif name == "triage-events":
            text = _triage_events(args.get("namespace"))
        elif name == "metrics":
            text = _metrics(args["namespace"], args.get("lookback"))
        else:
            raise ValueError(f"unknown prompt: {name}")
        return {"messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
```

### 5. `cli.py` — the `mcp` subcommand

```python
# in build_parser()
sub.add_parser("mcp", help="run k8sense as a stdio MCP server")

# in main()
if ns.command == "mcp":
    from k8sense.mcp_server.server import run_stdio
    return asyncio.run(run_stdio())
```

Single subcommand, no arguments. Reads stdin, writes stdout. Exits when the parent closes the pipe.

### 6. `agent.py` — consume the registry

`build_options()` is refactored to pull tools from the registry instead of importing `kubectl_tool` / `prometheus_tool` directly. The behaviour is identical; the source becomes single.

```python
from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from k8sense.tools.registry import all_tool_specs
# ... subagent imports unchanged ...

def build_options(system_prompt: str, model_id: str = DEFAULT_MODEL) -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions wired via the shared tool registry."""
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

The existing test `test_build_options_includes_kubectl_and_prometheus_tools` continues to work — both qualified names are still generated.

---

## Testing strategy

Inherits Phase 1/2's "strict TDD for deterministic code, eval harness for LLM behaviour" split.

**Unit (TDD):**

- `tools/registry.py` — `all_tool_specs()` shape; `KubectlInput` rejects empty args; `PrometheusInput` rejects malformed lookback; both accept valid values; `model_json_schema()` produces a non-empty dict.
- `mcp_server/resources.py` — namespace validation regex (positive + negative cases); each content function under success + failure (env-manipulated kubectl missing); `_recent_events_content` enforces the 30-line cap; `read_resource` URI dispatch (3 valid + 1 unknown raise).
- `mcp_server/prompts.py` — each helper's substrings; `_validate_namespace` raises on bad input; `get_prompt` dispatch (3 valid + 1 unknown raise + missing required arg → KeyError).
- `mcp_server/server.py` — `build_server()` returns a `Server`; `list_tools` returns specs; `call_tool` routes by name and validates via Pydantic; unknown tool raises.
- `agent.py` — existing tests stand; ensures the registry refactor doesn't regress the agents/tools wiring.

**Integration (smoke):**

- `tests/smoke/test_mcp_stdio.py` — spawn `.venv/bin/k8sense mcp` as a subprocess, write a `tools/list` JSON-RPC request, read the response, assert it contains `kubectl` and `prometheus_query`. Gated by `K8SENSE_ALLOW_API` like other smoke tests. Verifies the stdio entrypoint works end-to-end with the real `mcp` server library — no mocks.

**Eval harness:** no new entries required. The existing 15 entries still flow through the in-process SDK path. If Phase 3.1 reveals a need, add 2-3 stdio-roundtrip entries that simulate Claude Code attaching to k8sense MCP.

**No mocking** of kubectl, Prometheus, the SDK, or the MCP server. The "kubectl missing" scenarios use `monkeypatch.setenv("PATH", str(tmp_path))` — the same env-manipulation pattern as Phase 1/2.

---

## Error handling

| Failure                                          | Response                                                                          |
| ------------------------------------------------ | --------------------------------------------------------------------------------- |
| `k8sense mcp` started but cluster unreachable    | Server starts fine; tools/resources return error envelopes when called            |
| Unknown tool name in `call_tool`                 | `ValueError` propagated; MCP layer surfaces it to the client as an error response |
| Unknown URI in `read_resource`                   | Same — `ValueError`, surfaced to client                                           |
| Invalid namespace in `manifests/{namespace}` URI | Resource returns a markdown body explaining the rejection (no exception)          |
| Invalid namespace in a prompt argument           | `ValueError` raised in `_validate_namespace`; MCP surfaces it                     |
| Pydantic validation failure in `call_tool`       | `pydantic.ValidationError` propagates; MCP client sees a structured error         |
| `prometheus_query` while Prometheus is down      | Existing fallback applies — handler returns `exit_code=-1` envelope               |
| Client disconnects mid-call                      | `stdio_server` raises; the subprocess exits cleanly                               |
| Pipe closure on stdin                            | `server.run()` returns naturally; the subprocess exits 0                          |

---

## Cross-cutting concerns

- **Versioning.** Bump `pyproject.toml` to `0.3.0`; the SDK server `version="0.3.0"`; the MCP `Server("k8sense")` defaults to `version="1.0.0"` internally (not user-visible).
- **No new dependencies.** The `mcp` package is already a transitive dep of `claude-agent-sdk`.
- **README.** Add a Phase 3 section showing the Claude Code MCP config snippet and a short walkthrough of the three slash commands.
- **CLI surface.** New `k8sense mcp` subcommand. No flags in Phase 3.

---

## Likely Phase-3.1 follow-ups

By analogy to Phase 1.1 and Phase 2.1, expect 1-3 corrections once we run the live MCP server with Claude Code:

- **MCP package import paths.** The `mcp` package version installed transitively may use `mcp.server.lowlevel` or `mcp.server.fastmcp` instead of the top-level `mcp.server`. Verify at implementation; adjust imports.
- **`list_resource_templates` decorator may not exist.** Some `mcp` versions expose this as `@server.list_resource_templates()`, others as `@server.list_resourceTemplates()` (camelCase), and a few don't have it at all (resource templates are inferred from URI patterns in `list_resources()` instead). Verify at implementation; if absent, fold templates into `list_resources()`.
- **Resource template URI handling.** Claude Code might pass URI templates as either the literal `{namespace}` form or already-substituted by the time `read_resource` runs. Confirm via stdio probe and handle both.
- **Prompt argument shape.** Some MCP client libraries expect prompt arguments as `{"arguments": [{"name": "x", "value": "y"}]}` rather than `{"x": "y"}`. Verify against the Claude Code client behaviour.
- **Tool input schema format.** `model_json_schema()` produces a JSON Schema with `$defs` references; some MCP clients prefer inlined schemas. If Claude Code rejects the schema, switch to `model_json_schema(mode="serialization")` or inline manually.

These aren't surprises — they're expected.

---

## Success criteria

- **All 144 prior unit tests still pass** + ~40 new Phase 3 unit tests + the new stdio smoke test.
- **`k8sense ask "list namespaces"`** continues to work, dispatch through the in-process registry path, and produce the same outcome as Phase 2.
- **`k8sense mcp` runs over stdio** — manual test: pipe a `tools/list` JSON-RPC request to `.venv/bin/k8sense mcp`, see the two tools listed. Same for resources and prompts.
- **Claude Code session with k8sense in `mcpServers`** can:
  - Call `kubectl` and `prometheus_query` tools from within a conversation
  - Read the three resources via the resource picker
  - Invoke the three slash commands with parameter prompts
- **The eval suite passes ≥13/15** through the in-process path (carrying Phase 2's score forward).

## Reference

- Phase 2 spec: `docs/superpowers/specs/2026-05-26-k8sense-phase-2-design.md`
- Phase 2 plan: `docs/superpowers/plans/2026-05-26-k8sense-phase-2.md`
- Master spec: `docs/superpowers/specs/2026-05-25-k8sense-design.md` — Phase 3 outline section
- Claude Agent SDK 0.2.87 — `claude_agent_sdk.types.McpStdioServerConfig` confirms standard MCP stdio is supported (the SDK already speaks the same protocol we're publishing)
- MCP Python SDK — bundled as a transitive dep; provides `mcp.server.Server`, `mcp.server.stdio.stdio_server`
