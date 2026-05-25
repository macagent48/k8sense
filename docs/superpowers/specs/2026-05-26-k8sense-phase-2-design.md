# k8sense Phase 2 — Design Spec (Subagents)

**Date:** 2026-05-26
**Author:** Yash
**Status:** Draft, pending review
**Parent spec:** `docs/superpowers/specs/2026-05-25-k8sense-design.md`
**Predecessor:** Phase 1 at git tag `phase-1` (commit `fef513f`, +follow-up `38e6c25`)

## Goal

Add three specialised subagents to the existing k8sense CLI so that questions about logs, events, and metrics are investigated by purpose-built investigators with their own narrow prompts, tool subsets, and turn budgets. Introduce one new tool (`prometheus_query`) for time-series questions against the homelab's external Prometheus instance.

The orchestrator agent (built in Phase 1) gains nothing more than a system-prompt addendum explaining when to delegate; the SDK handles the actual dispatch via `ClaudeAgentOptions.agents={}` and `AgentDefinition.background=True`.

## Non-goals

- A Loki / log-aggregation tool. Logs come from `kubectl logs` only in Phase 2. Loki integration is a Phase 4/5 concern.
- A persistent memory or database. Subagents are stateless single invocations. The "we've seen this before" journal is Phase 4.
- Subagent nesting. Subagents do not dispatch to other subagents — flat orchestration only.
- Per-subagent cheaper models (Haiku for triage, Sonnet for analysis). Same model everywhere in Phase 2; the SDK's `model="inherit"` is used. Heterogeneous models become a Phase 3+ optimisation.
- PromQL plotting, sparklines, or chart rendering. Tool returns text only.
- Recording integration fixtures. Still deferred (carried from Phase 1).

## Decisions locked in during brainstorming

- **3 subagents at once**, not staged. `event_triager`, `log_investigator`, `metrics_analyst`.
- **`metrics_analyst` uses both `kubectl top` and Prometheus.** A new `prometheus_query` tool talks HTTP directly to `http://192.168.70.174:9090`; `K8SENSE_PROM_URL` overrides for remote/tunnel scenarios.
- **Subagents share the kubectl MCP tool**, scope is narrowed via each subagent's system prompt (and `maxTurns=8` per subagent). No new per-tool wrappers (e.g. `kubectl_logs`) in Phase 2.
- **Parallel dispatch**: `AgentDefinition.background=True` so the orchestrator can fan out to multiple subagents on multi-source questions.
- **No database.** Phase 4's incident journal remains the right time to introduce persistence.

## Architecture

The orchestrator agent from Phase 1 is reused without changes to its dispatch loop. The only Phase-2-relevant addition is wiring `agents={name: AgentDefinition}` into `ClaudeAgentOptions` in `build_options()`.

```
user question
   ↓
cli.ask → agent.run_ask
   ↓
ClaudeAgentOptions(
    system_prompt=...,                              # Phase 1 + delegation addendum
    mcp_servers={"k8sense": kubectl_server},        # Phase 1
    allowed_tools=["mcp__k8sense__kubectl",
                   "mcp__k8sense__prometheus_query"],     # +prometheus in Phase 2
    agents={                                        # NEW in Phase 2
        "event_triager":     event_triager.DEFINITION,
        "log_investigator":  log_investigator.DEFINITION,
        "metrics_analyst":   metrics_analyst.DEFINITION,
    },
    model="claude-sonnet-4-6",
)
   ↓ (streaming, SDK handles subagent dispatch)
   ├── orchestrator thinks
   ├── orchestrator calls Task tool with subagent_type=event_triager  ← dispatch
   │     ↓
   │     subagent runs in its own context window, returns final answer
   ├── orchestrator merges subagent findings with its own kubectl calls
   └── final answer streamed to renderer
```

## Repo layout (post-Phase 2)

```
src/k8sense/
├── cli.py                       # unchanged
├── agent.py                     # +10 lines: agents={...} wiring
├── prompts/system.py            # +1 paragraph: delegation guidance
├── tools/
│   ├── kubectl.py               # unchanged
│   └── prometheus.py            # NEW (~90 lines)
├── subagents/                   # NEW directory
│   ├── __init__.py              # re-exports the 3 definitions
│   ├── event_triager.py         # ~40 lines: AgentDefinition + prompt
│   ├── log_investigator.py      # ~45 lines
│   └── metrics_analyst.py       # ~50 lines (slightly longer prompt with PromQL examples)
└── render.py                    # +1 method: subagent_dispatch()

evals/
├── dataset.jsonl                # +5 multi-source entries (10 → 15)
└── runner.py                    # +1 fingerprint type: subagent_called

tests/unit/
├── test_prometheus_tool.py      # NEW: URL builder, response parser, envelope
├── test_subagents.py            # NEW: each subagent's prompt assertions
└── test_eval_runner.py          # +tests for the subagent_called fingerprint
```

---

## Component design

### 1. The three subagents

Each lives in its own module and exports a `DEFINITION: AgentDefinition`. All three share these defaults unless overridden below:

| Field            | Default                                                             |
| ---------------- | ------------------------------------------------------------------- |
| `tools`          | `["mcp__k8sense__kubectl"]`                                         |
| `model`          | `"inherit"` (same as orchestrator)                                  |
| `maxTurns`       | `8`                                                                 |
| `background`     | `True`                                                              |
| `permissionMode` | `"default"` (no auto-accepts; Phase 4 introduces gated remediation) |

#### `event_triager`

- **`description`**: `"Scans recent Kubernetes events in a given namespace (or cluster-wide) and ranks the most concerning ones by severity. Use when the user asks 'what's going wrong', 'recent events', or 'any warnings'."`
- **`prompt`** (~15 lines): role as a triager; conventions:
  - `kubectl get events --sort-by=.lastTimestamp -A` (or `-n <ns>` if scoped)
  - Filter `--field-selector type=Warning` when the user says "warnings" or "errors"
  - Summarise the top 5 by recency and severity: include `reason`, `count`, `firstTimestamp`, `lastTimestamp`, `object kind/name`
  - If no Warning events found, say so explicitly — don't fabricate concern
- **Tools**: kubectl only.

#### `log_investigator`

- **`description`**: `"Given a pod name and namespace, fetches logs and describe output to explain restarts, crashes, or anomalies. Use when the user asks 'why is pod X failing', 'what's in pod X's logs', or 'why is X crashlooping'."`
- **`prompt`** (~20 lines): role; conventions:
  - Start with `kubectl describe pod <name> -n <ns>` to read events on the pod and restart history
  - Then `kubectl logs <name> -n <ns> --tail=200`
  - If logs are empty (pod just restarted), try `--previous` to get the prior container's logs
  - Recognise common patterns: `OOMKilled` in describe → memory limit; `ImagePullBackOff` → image/registry; `CrashLoopBackOff` with empty current logs → look at `--previous`
  - Quote 2-3 concrete log lines in the final answer rather than paraphrasing
- **Tools**: kubectl only.

#### `metrics_analyst`

- **`description`**: `"Queries kubectl top and Prometheus for resource usage of pods, nodes, or workloads. Use for 'how much CPU/memory is X using', 'is anything near its limit', or historical trends ('how has CPU trended over the last hour')."`
- **`prompt`** (~25 lines): role; conventions:
  - For current snapshot questions → `kubectl top pods -n <ns>` or `kubectl top nodes`
  - For trend / historical questions → `prometheus_query` with `lookback="5m"` or `"1h"` as appropriate
  - Useful PromQL primitives, embedded in the prompt as canned examples:
    - `sum(rate(container_cpu_usage_seconds_total{namespace="X"}[2m])) by (pod)` — pod CPU rate
    - `container_memory_working_set_bytes{namespace="X"}` — pod memory snapshot
    - `node_load1` — node load average
    - `kube_pod_container_status_restarts_total` — restart counter
  - If Prometheus is unreachable, fall back to `kubectl top` and say so in the answer
- **Tools** (overrides the default): `tools=["mcp__k8sense__kubectl", "mcp__k8sense__prometheus_query"]`.

### 2. The Prometheus tool

`src/k8sense/tools/prometheus.py` mirrors the structure of `tools/kubectl.py`:

```python
DEFAULT_PROM_URL = "http://192.168.70.174:9090"
DEFAULT_TIMEOUT_S = 10.0
MAX_RESULT_LINES = 50
MAX_RESULT_CHARS = 8000

async def run_prometheus_query(
    query: str,
    lookback: str | None = None,    # e.g. "5m", "1h"; None → instant query
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute a PromQL query. Returns {stdout, stderr, exit_code}.

    instant: GET /api/v1/query?query=<q>
    range:   GET /api/v1/query_range?query=<q>&start=<t-l>&end=<t>&step=<auto>
    """
```

The handler returns a kubectl-envelope-style result so the agent's `parse_handler_envelope` keeps working:

```
$ promql instant "node_load1"
exit_code=0
--- stdout ---
<truncated result lines>
```

- **URL resolution**: `os.environ.get("K8SENSE_PROM_URL", DEFAULT_PROM_URL)`. No discovery logic in Phase 2.
- **Step size for range queries**: `step = max(15s, lookback / 60)` — guarantees ≤60 buckets per query so results stay readable.
- **Result truncation**: 50 lines / 8000 chars maximum, with a `… (truncated)` suffix matching the kubectl truncation pattern.
- **Error envelope**: connection refused / timeout / 5xx → `exit_code=-1`, `stderr` describes the failure; Prometheus `{status: "error"}` → `exit_code=1`, `stderr` contains the parsed error.
- **SDK wrapper**: same two-name pattern as `kubectl_handler` / `kubectl_tool` (plain async function `prometheus_handler` for tests; `prometheus_tool` is the `SdkMcpTool` registration object).

**New dependency**: `httpx` (async HTTP client). One line added to `pyproject.toml` deps.

### 3. Orchestrator system-prompt addendum

`prompts/system.py`'s `_TEMPLATE` gets a new paragraph appended after the existing conventions block, before the topology snapshot:

```
You have specialised investigators available as subagents. Delegate to them
when a question is narrow enough to fit one of their descriptions:
- event_triager — for cluster events and warnings
- log_investigator — for pod-specific log questions
- metrics_analyst — for resource-usage and trend questions
For broad multi-source questions (e.g. "give me a health summary"), dispatch
multiple subagents in parallel and merge their findings. For simple direct
questions ("list namespaces", "describe deployment X"), just use kubectl
yourself.
```

### 4. Renderer addition

`Renderer.subagent_dispatch(name: str, brief: str)` — prints a cyan-bold marker so the user can see dispatch events in the stream:

```
↳ dispatching event_triager: scan argocd namespace for recent warnings
```

The marker is detected from the SDK stream by inspecting `ToolUseBlock` calls with the SDK's `Task` tool name (the SDK exposes subagent invocations as a `Task` tool call with `subagent_type` and `prompt`/`description` in the input). The exact extraction logic is verified at implementation time; the renderer interface stays stable regardless.

### 5. Eval expansion

`evals/dataset.jsonl` grows from 10 to 15 entries with these multi-source questions:

| id                       | question                                                                                           | required fingerprints                                                                                     |
| ------------------------ | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | -------- |
| `events-argocd`          | "Are there any recent warning events in argocd?"                                                   | `subagent_called: event_triager`, substring `argocd`                                                      |
| `why-restarts`           | "Why has argocd-server been restarting?"                                                           | `subagent_called: log_investigator`, `tool_args_contains: argocd-server`                                  |
| `top-memory-monitoring`  | "Which pod in monitoring is using the most memory?"                                                | `subagent_called: metrics_analyst`, `tool_args_contains: monitoring`                                      |
| `cpu-trend-30m`          | "Has CPU usage on the cluster trended up over the last 30 minutes?"                                | `subagent_called: metrics_analyst`, regex `\d+(\.\d+)?\s\*(%                                              | cores?)` |
| `cluster-health-summary` | "Give a one-paragraph health summary covering events, the busiest pod's logs, and resource usage." | `subagent_called: event_triager`, `subagent_called: log_investigator`, `subagent_called: metrics_analyst` |

The eval scorer gains a new fingerprint type `subagent_called` with semantics analogous to `tool_called`:

```python
elif kind == "subagent_called":
    if not any(
        tc.get("name") == "Task" and (tc.get("input") or {}).get("subagent_type") == value
        for tc in result.tool_calls
    ):
        failures.append(f"subagent '{value}' was never dispatched")
```

(The exact SDK tool name for subagent dispatches is verified at implementation time; the field name `subagent_type` may differ. The fingerprint contract stays stable.)

---

## Testing strategy

Inheriting Phase 1's "strict TDD for deterministic code, eval harness for LLM behaviour" split. New work in Phase 2:

**Deterministic (TDD):**

- `tools/prometheus.py`: URL builder, range-vs-instant dispatch, response parser, error envelope shape, truncation thresholds.
- `subagents/<name>.py`: prompt-assembly tests (e.g. `assert "OOMKilled" in event_triager.DEFINITION.prompt`).
- `evals/runner.py`: new `subagent_called` fingerprint type, including its failure path.

**LLM behaviour (evals):**

- 5 new multi-source eval entries (above).
- One eval entry that should NOT invoke any subagent ("list all namespaces") — verifies the orchestrator still answers directly when no delegation is needed.

**Integration (smoke):**

- One new smoke test: `K8SENSE_ALLOW_API=1 pytest -m smoke` asks `"give a cluster health summary"` and asserts ≥2 distinct subagents fired. Real cluster, real Prometheus.

**Explicitly no mocking** of Prometheus, kubectl, or the SDK. The Prometheus connection-refused test uses a deliberately-invalid `K8SENSE_PROM_URL` (env manipulation, not mocking).

---

## Error handling

| Failure                                                   | Response                                                                                                                                                    |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Prometheus unreachable (connection refused, DNS, timeout) | tool returns `exit_code=-1` with descriptive stderr; metrics_analyst's prompt teaches it to fall back to `kubectl top`                                      |
| Prometheus returns `{status: "error"}`                    | tool returns `exit_code=1` with the parsed PromQL error; model can fix and retry                                                                            |
| Malformed PromQL from the model                           | same as above                                                                                                                                               |
| Subagent exceeds `maxTurns=8`                             | SDK terminates the subagent and returns a partial result; orchestrator decides whether to retry, escalate, or answer with what it has                       |
| Subagent raises (network, SDK error)                      | propagates to the orchestrator's stream; the CLI's existing top-level try/except (from Phase 1's `cli.main` follow-up) catches and renders a one-line error |
| Parallel dispatch: one subagent fails, one succeeds       | Per-subagent failures isolated by the SDK. Orchestrator answers with what it has                                                                            |
| Result truncation triggered (>50 lines or >8000 chars)    | append `… (truncated)` suffix, same as kubectl's truncation in `render.py`                                                                                  |

---

## Phase-2.1 follow-ups (likely)

Things we may discover and fix mid-implementation, by analogy to the Phase 1 follow-ups (`fef513f`, `38e6c25`):

- The SDK's subagent dispatch name in `ToolUseBlock` may differ from "Task" — update the renderer detection + eval fingerprint accordingly.
- `httpx` may have a quirk with async timeouts vs `asyncio.wait_for` — adjust if needed.
- Subagent descriptions may need wording tweaks once we see how the orchestrator interprets them (real eval runs are the test).

These are tracked as expected, not surprises.

---

## Success criteria

- **All 15 eval cases pass** on the live cluster + Prometheus (carrying Phase 1's 10/10 forward).
- **`k8sense ask "why is argocd-server restarting?"`** triggers `log_investigator`, which produces a coherent answer citing log lines.
- **`k8sense ask "give a cluster health summary"`** dispatches at least two subagents in parallel and produces a 1-paragraph answer.
- **`k8sense doctor`** still reports three greens. (No new doctor checks needed — `K8SENSE_PROM_URL` is optional, default works for this environment.)
- **All 72 prior unit tests pass**, plus ~25 new unit tests across the new components, plus 16 (was 10) eval entries.

## Reference

- Phase 1 spec: `docs/superpowers/specs/2026-05-25-k8sense-design.md` (Phase 2 outline section is the seed for this spec)
- Phase 1 plan: `docs/superpowers/plans/2026-05-25-k8sense-phase-1.md`
- Monitoring architecture: `~/codes/homelab-k8s/monitoring/ARCHITECTURE.md` — confirms Prometheus at `192.168.70.174:9090`, SSH host `loki` for the same VM
- Claude Agent SDK 0.2.87 — `AgentDefinition` in `claude_agent_sdk.types`; `ClaudeAgentOptions.agents` accepts `dict[str, AgentDefinition]`
