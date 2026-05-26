# k8sense

A Claude Agent SDK-powered SRE for the homelab-k3s Kubernetes cluster. The codebase is structured as a **staged curriculum** — each phase adds one Agent SDK concept and ships standalone. See [the design spec](docs/superpowers/specs/2026-05-25-k8sense-design.md) for the full 5-phase roadmap.

| Phase | Tag                                                            | Capability                                             | Concepts introduced                                          |
| ----- | -------------------------------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------ |
| **1** | [`phase-1`](#phase-1--one-shot-investigator)                   | `k8sense ask` — single agent investigates with kubectl | Agent loop, custom tools, streaming, eval harness            |
| **2** | [`phase-2`](#phase-2--parallel-subagents--prometheus)          | Parallel subagent dispatch + Prometheus tool           | `AgentDefinition`, `background=True`, multi-tool MCP server  |
| **3** | [`phase-3`](#phase-3--mcp-server)                              | `k8sense mcp` stdio server — Claude Code can attach    | MCP `Server`, tools/resources/prompts, Pydantic JSON Schemas |
| **4** | [`phase-4`](#phase-4--hooks-permission-modes-incident-journal) | `--auto-fix` / `--propose` modes + incident journal    | PreToolUse hooks, permission modes, persistent memory        |
| 5     | —                                                              | Sentinel daemon                                        | (See spec.)                                                  |

---

## Install

```bash
# With pip:
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Or with uv:
uv sync --extra dev
```

## Authentication

k8sense uses whatever auth your `claude` CLI is logged into by default (OAuth via Claude Code, billed against your Claude subscription). No `ANTHROPIC_API_KEY` needed.

```bash
# Optional: only if you want to bill against the Anthropic API directly:
cp .env.example .env  # then edit and add ANTHROPIC_API_KEY
```

## Quick start

```bash
k8sense doctor                              # ✓ kubectl, ✓ kubeconfig, ✓ auth
k8sense ask "list every namespace"          # simple direct question
```

---

## Phase 1 — one-shot investigator

The starter phase. `k8sense ask` opens an agent that has exactly one tool — a read-only `kubectl` — and answers questions about the cluster by running commands and reasoning over the output. Streamed live with `rich`-rendered panels for each tool call.

### Phase 1 usage

```bash
k8sense ask "why is the argocd-server pod restarting?"
k8sense ask "anything weird in the longhorn namespace?"
k8sense ask "give me a one-paragraph health summary of the cluster"
k8sense ask "what's the cluster version?"
```

### What you'll see

```
↳ kubectl get pods -n argocd
exit_code=0
--- stdout ---
NAME                                READY   STATUS    RESTARTS   AGE
argocd-server-7d9f6c5d8c-x2m4l      1/1     Running   3 (5m ago) 1h
...

(model investigates further, then:)

╭─ argocd-server is healthy now ─────────────────────────────────────╮
│ The pod has 3 restarts but is currently Running and ready. The      │
│ most recent restart was 5 minutes ago, likely an OOM event — its    │
│ memory limit is 256Mi and the working set hovers around 240Mi.      │
╰─────────────────────────────────────────────────────────────────────╯
```

### Phase 1 architecture

- `src/k8sense/cli.py` — argparse entrypoint (`ask`, `doctor`)
- `src/k8sense/agent.py` — assembles `ClaudeAgentOptions`, drives the streaming receive loop, parses kubectl envelopes
- `src/k8sense/tools/kubectl.py` — read-only verb allowlist (`get`, `describe`, `logs`, `top`, `events`, `version`), async subprocess wrapper, `@tool`-wrapped for the SDK
- `src/k8sense/prompts/system.py` — homelab-k3s SRE framing + startup topology snapshot
- `src/k8sense/render.py` — `rich`-based renderer (thinking → dim, tool calls → cyan, results → coloured panels)
- `evals/` — fingerprint-based eval harness, scored against the live cluster

---

## Phase 2 — parallel subagents + Prometheus

Phase 2 adds three specialised subagents and a Prometheus tool. The orchestrator agent from Phase 1 is unchanged at the loop level — but it now has `agents={}` wired in and a delegation paragraph telling it when to dispatch.

| Subagent           | Tools                      | Use when                                           |
| ------------------ | -------------------------- | -------------------------------------------------- |
| `event_triager`    | kubectl                    | "recent events", "what's going wrong", "warnings"  |
| `log_investigator` | kubectl                    | "why is pod X failing", "what's in the logs"       |
| `metrics_analyst`  | kubectl + prometheus_query | "how much CPU/memory is X using", trends over time |

Subagents run **in parallel** (`background=True`) when the question is multi-source. Each has `maxTurns=8` and its own narrow system prompt — they don't dispatch to other subagents.

### Phase 2 usage

Simple questions still answer directly (no subagent dispatch — Phase 1 behaviour preserved):

```bash
k8sense ask "list all namespaces"           # no dispatch, just kubectl
```

Narrow questions go to one subagent:

```bash
k8sense ask "why is the argocd-server pod restarting?"
# ↳ dispatching log_investigator: ...

k8sense ask "are there any recent warnings in argocd?"
# ↳ dispatching event_triager: ...

k8sense ask "how has CPU trended over the last hour?"
# ↳ dispatching metrics_analyst: ...
```

Broad multi-source questions dispatch multiple in parallel:

```bash
k8sense ask "give me a one-paragraph health summary covering events, logs of the busiest pod, and current resource usage"
# ↳ dispatching event_triager: scan recent warnings
# ↳ dispatching metrics_analyst: identify resource-heavy pods
# ↳ dispatching log_investigator: investigate <busiest pod>
# (each runs concurrently; orchestrator merges their findings)
```

### Prometheus access

`metrics_analyst` queries PromQL against the homelab Prometheus VM (default `http://192.168.70.174:9090`). Override with:

```bash
export K8SENSE_PROM_URL="http://prometheus.example.com:9090"
```

If Prometheus is unreachable, `metrics_analyst` automatically falls back to `kubectl top` for current-state queries.

### Phase 2 architecture (additions only)

- `src/k8sense/tools/prometheus.py` — async PromQL client (`httpx`), supports instant and range queries via `lookback="5m"`/`"1h"`/`"24h"`; matches kubectl's `{stdout, stderr, exit_code}` envelope so the agent loop parses both uniformly
- `src/k8sense/subagents/` — three `AgentDefinition` modules, re-exported from `__init__.py`
- `agent.py` — `build_options()` now wires `agents={}` with the three definitions; `SUBAGENT_DISPATCH_TOOL_NAME = "Agent"` so the renderer can detect dispatches in the SDK stream
- `render.py` — new `subagent_dispatch(name, brief)` method (cyan-bold `↳ dispatching <name>: ...` marker)
- `evals/runner.py` — `subagent_called` and `subagent_not_called` fingerprint types
- `evals/dataset.jsonl` — grows from 10 to 15 entries (5 new multi-source questions verifying dispatch behaviour)

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

---

## Phase 4 — hooks, permission modes, incident journal

Phase 4 is where k8sense gains real mutation capability — with safety rails.

### Permission modes

| Mode                 | What the hook does                                                     | How to invoke                  |
| -------------------- | ---------------------------------------------------------------------- | ------------------------------ |
| `readonly` (default) | Blocks ALL mutations                                                   | `k8sense ask "..."`            |
| `propose`            | Allowlisted mutations → printed as copy-paste suggestion, NOT executed | `k8sense ask --propose "..."`  |
| `auto-safe`          | Allowlisted mutations execute; everything else blocked                 | `k8sense ask --auto-fix "..."` |

Precedence: CLI flag > `K8SENSE_PERMISSION_MODE` env var > `~/.k8sense/config.toml` > default. Check the active mode with `k8sense doctor`.

### Safe-action allowlist

Only these four kubectl mutations may run in `auto-safe`:

1. `kubectl delete pod <name>` — only if pod is `CrashLoopBackOff` / `ImagePullBackOff` / `Error` / `Unknown` / `Pending` (status checked live before each delete)
2. `kubectl rollout restart deployment/<name>`
3. `kubectl cordon <node>` (reversible; no eviction)
4. `kubectl delete pod --field-selector=status.phase=Succeeded` (cleanup)

Everything else (`apply`, `scale`, `delete deployment`, `drain`, ...) is denied even in `auto-safe`. Phase 4.1+ may grow the list.

### Incident journal

Every `k8sense ask` invocation appends one line to `~/.k8sense/journal/incidents.jsonl`:

```json
{"timestamp": "2026-05-26T...", "signature": {"kind": "Pod", "namespace": "argocd", "name": "argocd-server-7d", "reason": "OOMKilled"}, "resolution": "...", "actions_taken": [...], "mode": "auto-safe", ...}
```

At the start of each new investigation, k8sense looks up similar prior incidents (tiered: exact match → namespace+reason → reason) and injects up to 5 into the prompt so the agent knows "we've seen this before."

Browse manually:

```bash
cat ~/.k8sense/journal/incidents.jsonl | jq -s 'sort_by(.timestamp) | reverse | .[:5]'
```

### Try it

```bash
# Default: readonly investigation, like Phases 1-3
k8sense ask "why is argocd-server restarting?"

# Propose mode: agent suggests a fix; you copy-paste
k8sense ask --propose "argocd-server is OOMKilled; restart it"

# Auto-fix mode: agent executes (if allowlisted)
k8sense ask --auto-fix "argocd-server is OOMKilled; restart it"
```

---

## Run the eval suite

Scores the agent's behaviour against 15 fingerprinted cluster questions. Requires a reachable cluster + Prometheus.

```bash
python -m evals.runner
cat evals/report.md
```

Last live run: **13/15 pass** (the 2 remaining failures are fingerprint fragility, not implementation defects — see the Phase-2.1 follow-up notes in the spec).

## Run the test suite

```bash
pytest                                  # unit tests (no API spend, no network)
K8SENSE_ALLOW_API=1 pytest -m smoke     # end-to-end against the real cluster
```

Current count: **291 unit tests** + 4 smoke (skipped by default).

## Architecture (full)

```
src/k8sense/
├── cli.py                       # argparse: ask / doctor / mcp; +flags --propose --auto-fix
├── agent.py                     # ClaudeAgentOptions wiring, hook + journal integration
├── prompts/system.py            # SRE framing + delegation paragraph + topology snapshot
├── permissions.py               # Phase 4: PermissionMode enum + resolution (flag > env > config)
├── tools/
│   ├── kubectl.py
│   ├── prometheus.py
│   └── registry.py
├── subagents/
│   ├── event_triager.py
│   ├── log_investigator.py
│   └── metrics_analyst.py
├── hooks/                       # Phase 4: PreToolUse gating
│   ├── safe_actions.py          #   pure: parse_kubectl, is_allowlisted, decide
│   └── pre_tool_use.py          #   async SDK hook callback
├── memory/                      # Phase 4: incident journal
│   ├── signature.py             #   extract Signature from completed investigation
│   └── journal.py               #   JSONL append + tiered similarity lookup
├── mcp_server/
│   ├── server.py
│   ├── resources.py
│   └── prompts.py
└── render.py

evals/
├── dataset.jsonl                # 15 fingerprinted questions
└── runner.py                    # scorer + live driver

tests/
├── unit/                        # 291 tests, strict TDD
├── integration/                 # (deferred — Phase 5)
└── smoke/                       # 4 end-to-end tests against real cluster
```

## License

[MIT](LICENSE) — feel free to learn from, fork, or build on this curriculum.
