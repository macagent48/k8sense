# k8sense — Design Spec

**Date:** 2026-05-25
**Author:** Yash
**Status:** Draft, pending review

## Goal

Build `k8sense`, a CLI + sentinel that observes a homelab k3s cluster, investigates anomalies using the Claude Agent SDK, and (when permitted) auto-remediates a whitelisted set of safe actions. The project doubles as a **structured curriculum** for going from beginner to advanced on the Claude Agent SDK — each phase introduces one new SDK concept and remains shippable on its own.

Target cluster: existing `homelab-k3s` (see `~/codes/homelab-k8s`). Single cluster, single user, posts findings to Telegram.

## Non-goals

- Replacing Prometheus / Grafana / existing monitoring stack. `k8sense` sits on top, doing intelligent triage and remediation.
- Multi-cluster support.
- A web UI.
- A long-lived agent "session" that accumulates context across incidents. (See Autonomous Mode Design for why we deliberately use one-shot invocations.)
- Aggressive auto-repair beyond a tight, hook-enforced safe-action allowlist.

## Phase Roadmap

The repo is a single Python package built up in five phases. The **core agent (`agent.py`) is the same in every phase**; what changes is _how it's invoked_ and _what it's allowed to do_.

| Phase | Ships                                                                    | SDK concepts learned                                                                     |
| ----- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| **1** | `k8sense ask "<question>"` — one-shot investigative CLI                  | Agent loop, system prompts, custom tools, streaming, prompt caching, eval harness        |
| **2** | Same CLI, agent dispatches parallel subagents for deep investigation     | Subagents (`AgentDefinition`), parallel orchestration, context isolation, result merging |
| **3** | `k8sense mcp` — same operations exposed as MCP server                    | MCP server authoring, tool schemas, stdio transport, reuse from Claude Code              |
| **4** | `k8sense ask --auto-fix` flag — propose & gated-execute remediations     | Hooks (PreToolUse), permission modes, persistent memory / incident journal               |
| **5** | `k8sense watch` — long-running sentinel, Telegram, auto-safe remediation | Event-driven invocation, trigger pipelines, durable state, graceful shutdown             |

Each phase is independently mergeable. Stopping at any phase still leaves a useful tool.

> **Implementation plans:** this spec is the umbrella design. Each phase will get its own implementation plan when work on that phase begins. We do not write all five plans up-front — earlier phases inform the shape of later ones.

## Repo Layout (final state at end of Phase 5)

```
k8sense/
├── pyproject.toml
├── README.md
├── .env.example
├── src/k8sense/
│   ├── __init__.py
│   ├── cli.py                   # entrypoint: ask / mcp / watch / doctor
│   ├── agent.py                 # core agent loop (used by every phase)
│   ├── prompts/
│   │   └── system.py            # system prompt + topology snapshot
│   ├── tools/                   # Phase 1+
│   │   ├── kubectl.py
│   │   ├── logs.py              # Phase 2
│   │   └── metrics.py           # Phase 2
│   ├── subagents/               # Phase 2+
│   │   ├── event_triager.py
│   │   ├── log_investigator.py
│   │   └── metrics_analyst.py
│   ├── mcp_server/              # Phase 3
│   │   └── server.py
│   ├── hooks/                   # Phase 4
│   │   └── pre_tool_use.py
│   ├── memory/                  # Phase 4
│   │   └── journal.py
│   ├── sentinel/                # Phase 5
│   │   ├── watcher.py           # event stream
│   │   ├── filter.py            # debounce / dedup / cooldown
│   │   ├── synth.py             # event → prompt
│   │   └── daemon.py            # wires the pipeline
│   ├── notify/                  # Phase 5
│   │   └── telegram.py
│   └── render.py                # rich-based CLI rendering
├── evals/
│   ├── dataset.jsonl            # cluster questions + expected fingerprints
│   └── runner.py
└── tests/
    ├── unit/
    ├── integration/             # recorded SDK fixtures
    └── smoke/                   # against real cluster
```

---

## Testing Strategy

The project mixes two kinds of code that need fundamentally different testing disciplines. Calling everything "tests" hides the split, so we name it explicitly.

### Deterministic code → strict TDD

Applies to: custom tools, allowlist enforcement, render/formatting, prompt assembly, filter rules (dedup, cooldown, rate limits), journal lookup, signature matching, hook logic, MCP schemas.

Workflow for every unit of deterministic code:

1. **Red** — write a failing test that pins the behaviour ("verb 'delete' is rejected with exit_code -1 and a useful stderr").
2. **Green** — minimum implementation to make the test pass. No flourishes.
3. **Refactor** — clean up with the test as a safety net.

We use the `superpowers:test-driven-development` skill to enforce this in the implementation plan — every deterministic step lists its failing test before its implementation step.

**No mocking of `kubectl` or the SDK in these tests.** Allowlist rejection paths can be tested without a cluster. Success paths run against a real cluster's safe namespace (`kube-system`, read-only commands only). Mocked infrastructure tests are a known trap from previous incidents and are not used here.

### LLM behaviour → eval harness, NOT TDD

Applies to: does the agent investigate before concluding, does it dispatch the right subagent, does it propose the right remediation, does it consult memory when relevant.

This is not a TDD candidate. The output depends on a model, not on our code. A test that says `assert "delete pod" in output` is fake confidence — the next prompt tweak silently breaks it.

Instead:

- `evals/dataset.jsonl` defines a curated set of cluster questions, each paired with **expected-answer fingerprints** (substring matches, regex patterns, structural assertions like "calls the `kubectl_get_pods` tool at least once").
- `evals/runner.py` executes the agent against every question and reports fingerprint pass/fail.
- The evalset grows phase by phase. Phase 1 starts with ~10 entries; Phase 2 adds multi-source questions; Phase 4 adds remediation-correctness questions.
- A failing fingerprint blocks the phase merge.

### Integration → recorded fixtures, no API spend in CI

For end-to-end agent flows (`k8sense ask "..."` produces a coherent investigation), we record a small number of real SDK transcripts against the live cluster, version them in `tests/integration/fixtures/`, and replay them in CI. This catches regressions in the assembly without re-billing the API on every commit.

### Smoke → real cluster, manual or scheduled

One end-to-end test per phase, against the live `homelab-k3s`. Not run on every commit. Not blocking by default. Acts as the final reality check before tagging a phase release.

### Summary table

| Layer                      | Discipline                              | Where                |
| -------------------------- | --------------------------------------- | -------------------- |
| Deterministic units        | **Strict TDD** (red → green → refactor) | `tests/unit/`        |
| End-to-end agent flow      | Recorded SDK fixtures, replayed in CI   | `tests/integration/` |
| LLM reasoning quality      | **Eval harness** (fingerprint matches)  | `evals/`             |
| Live cluster reality check | Smoke run per phase                     | `tests/smoke/`       |

---

## Phase 1 — Detailed Design

Phase 1 is where we start coding. It is intentionally the deepest section.

### 1.1 Behaviour

```
$ k8sense ask "why is the argocd-server pod restarting?"
$ k8sense ask "anything weird in the longhorn namespace?"
$ k8sense ask "give me a health summary of the cluster"
```

The agent investigates by running `kubectl` (read-only), reasons over the output, and streams a written explanation back to the terminal with `rich`-rendered panels for each tool call.

### 1.2 Core agent (`agent.py`)

- Uses `ClaudeSDKClient` from `claude-agent-sdk` in streaming mode.
- Single system prompt (`prompts/system.py`) framing the agent as a "homelab k3s SRE": cluster name, conventions, expectation to investigate before concluding.
- **Prompt caching** enabled on:
  - the system prompt (~static)
  - a "cluster topology snapshot" block injected once per process: `kubectl get ns,nodes -o wide` — small (~1-2KB), refreshed only at startup.
- Loop terminates when the model emits no further tool calls.
- Soft cap: 20 tool calls per query, then abort with an explanatory message.

### 1.3 Custom tool (`tools/kubectl.py`)

- One tool registered via `@tool` decorator and `create_sdk_mcp_server` (in-process).
- Signature: `kubectl(args: list[str]) -> {stdout: str, stderr: str, exit_code: int}`.
- **Allowlist enforced inside the tool itself** (not via hooks; hooks come in Phase 4 and we want a clear before/after):
  - Allowed verbs: `get`, `describe`, `logs`, `top`, `events`, `version`
  - Everything else rejected with `{exit_code: -1, stderr: "verb '<X>' not allowed in read-only mode"}`
- Timeout: 15s per command. On timeout: `{exit_code: -1, stderr: "timeout after 15s"}`.
- The model handles errors itself (retry with different args, give up, etc.) — no Python-side retry logic.

### 1.4 CLI (`cli.py`)

- `argparse`, no click.
- Subcommands: `ask <question>` (Phase 1), `doctor` (env check). Later phases add `mcp`, `watch`.
- `ask`:
  - Builds system prompt + topology snapshot
  - Instantiates `ClaudeSDKClient`
  - Streams output via `render.py`
  - Exit 0 on completion, 1 on agent error

### 1.5 Output rendering (`render.py`)

Uses the `rich` library:

- Model thinking → dimmed text
- Tool call → collapsible panel with command in header and truncated stdout in body
- Final answer → bold panel with markdown rendering

### 1.6 Eval harness

Set up in Phase 1 so Phases 2-5 inherit it for free.

- `evals/dataset.jsonl` — JSONL of cluster questions paired with **expected-answer fingerprints** (substrings, regex, or structural checks), not exact text matches.
- ~10 questions to start (e.g., "list namespaces", "is longhorn healthy", "explain why pod X restarted").
- `evals/runner.py` runs each question against the agent, scores fingerprint matches, and emits a markdown report.
- Run before each phase merge; failing fingerprints block the merge.

### 1.7 Error handling (Phase 1)

| Failure                                   | Response                                                                 |
| ----------------------------------------- | ------------------------------------------------------------------------ |
| `kubectl` not installed                   | Fail fast at startup with install hint.                                  |
| Cluster unreachable                       | Topology snapshot fetch fails → exit 1 with message before agent starts. |
| Tool call timeout                         | Returned to agent as error string; agent decides next step.              |
| SDK API error (network, rate limit, auth) | Bubble up, print one-line message, exit 1. No retry loop.                |
| Model runaway (≥20 tool calls)            | Abort with "investigation hit max steps" message.                        |

### 1.8 Testing

Three layers, deliberately no SDK or kubectl mocking:

1. **Unit tests** — kubectl allowlist rejection paths, render formatting, prompt assembly. No network.
2. **Integration tests** — recorded SDK transcripts replayed in CI. Real fixtures captured against a real cluster, version-controlled, no API spend on every PR.
3. **Smoke test** — one real end-to-end run against the live `homelab-k3s` for `k8sense ask "list namespaces"`. Manual or nightly schedule, not on every commit.

---

## Phase 2 — Subagents (outline)

Three specialised agents via `AgentDefinition`:

- **`event_triager`** — scans recent events, ranks by severity. Tools: `kubectl get events`.
- **`log_investigator`** — given a pod, fetches & summarises logs. Tools: `kubectl logs` with `--previous`/`--tail` variants.
- **`metrics_analyst`** — given a workload, queries `kubectl top`. (Prometheus integration optional, deferred until needed.)

Top-level agent decides when to dispatch and merges findings. Independent investigations dispatched in parallel. Each subagent has its own context window — keeps the main agent uncluttered.

Eval set expanded with multi-source questions ("compare restart counts across argocd and longhorn") to verify subagent dispatch.

## Phase 3 — MCP server (outline)

- New entrypoint `k8sense mcp` — runs an MCP server over stdio.
- Tools previously in-process are exposed via the MCP protocol with Pydantic-defined schemas.
- Phase 1/2 agent code is refactored to call the MCP server instead of in-process tools. Behaviour identical; transport boundary explicit.
- **Side benefit:** add `k8sense` to your Claude Code MCP config and now your day-to-day Claude Code sessions can investigate the cluster too.
- Resources expose static cluster docs (e.g., `mcp://k8sense/manifests/argocd`).

## Phase 4 — Hooks, permissions, memory (outline)

### Hooks

- **PreToolUse hook** intercepts every `kubectl` invocation.
- Parses verb + (optionally) resource. Mutating verbs (`delete`, `apply`, `scale`, `rollout restart`, `patch`, `edit`, `replace`, `cordon`, `drain`) require both `--auto-fix` mode AND membership in the safe-action allowlist.
- Initial safe-action allowlist:
  - `kubectl delete pod <name>` (forces reschedule)
  - `kubectl rollout restart deployment/<name>`
- Denies returned to the agent as tool errors so it can re-plan or escalate.

### Permission modes

- `readonly` — hook blocks ALL mutations regardless of allowlist. Default.
- `propose` — hook converts mutations into a draft command surfaced in the output channel (CLI stdout in Phase 4, Telegram in Phase 5). Nothing is executed.
- `auto-safe` — hook enforces the allowlist; whitelisted mutations execute, others are rejected.

### Memory (incident journal)

- Stored as JSONL under `~/.k8sense/journal/`.
- Each investigation appends an entry: `{timestamp, question, summary, tools_called, resolution, signature, severity}`.
- `signature` is a structured key (e.g., `{kind: Pod, namespace, name, reason}`) so similar incidents can be found cheaply.
- At the start of each new investigation, the 5 most-similar prior incidents are fetched by signature match and injected into the prompt → "We've seen this before, last time it was X, resolution was Y."

## Phase 5 — Sentinel (outline)

See the next section for the architectural detail of how autonomous mode works.

Concrete deliverables:

- `k8sense watch` command.
- Streams the Kubernetes events API.
- Filters / debounces / cooldowns events before invoking the agent.
- Runs the Phase 4 agent (in `auto-safe` mode by default) on each surviving event.
- Posts findings to Telegram (uses existing `plugin:telegram:telegram` channel via reply tool, or via direct bot API — TBD per Phase 5 design).
- Persists state under `~/.k8sense/state/` so a restart resumes the journal but skips already-processed events.
- `--dry-run` mode posts what it _would_ do without executing.

---

## Autonomous Mode Design (Phase 5)

### Core insight

The agent itself doesn't change between phases. It is reactive in every phase. What changes in Phase 5 is **who writes the prompt**: in Phase 1 a human types it, in Phase 5 a small piece of non-AI code synthesises it from cluster events.

There is no "always-on AI." There is a reactive AI sitting behind a trigger pipeline that makes the system _look_ proactive.

### Pipeline

Five components, only one of which is AI:

```
   Kubernetes API
        │
        ▼
   ┌─────────────┐
   │ 1. Watcher  │  kubectl get events --watch -A
   │  (no AI)    │  emits raw event stream
   └─────────────┘
        │
        ▼
   ┌─────────────┐
   │ 2. Filter   │  drops Normal events, dedupes, debounces,
   │  (no AI)    │  applies cooldowns, hard rate limits
   └─────────────┘
        │
        ▼
   ┌─────────────┐
   │ 3. Synth    │  turns event into a natural-language prompt
   │  (no AI)    │  includes current permission mode
   └─────────────┘
        │
        ▼
   ┌─────────────┐
   │ 4. AGENT    │  Phase 1 agent, unchanged
   │   (AI)      │  + Phase 2 subagents
   │             │  + Phase 4 hook + memory + permissions
   └─────────────┘
        │
        ▼
   ┌─────────────┐
   │ 5. Outcome  │  posts summary to Telegram
   │  (no AI)    │  writes incident to journal
   │             │  records cooldown for affected resources
   └─────────────┘
```

### Filter layer rules

Defence in depth starts here, _before_ any AI is invoked. Cheap, rule-based, predictable:

- **Severity floor:** drop `Normal` events.
- **Dedup window:** drop if same `(kind, namespace, name, reason)` seen in last 5 min.
- **Cooldown after action:** if Outcome recently touched `(namespace, workload)`, ignore further events on it for 30 min.
- **Hard rate limit:** max N agent invocations per hour (configurable, default 20).
- **Allowlisted namespaces:** opt-in list of namespaces the sentinel is permitted to act in (start with `argocd`, `longhorn`, expand later).

### Walkthrough — one event

Cluster emits:

```
Warning  BackOff  pod/argocd-server-7d-9xk2  Back-off restarting failed container
```

1. **Watcher** receives the event.
2. **Filter** checks severity (Warning ≥ floor), dedup window (clear), cooldown (clear), rate limit (under budget), namespace (`argocd` allowlisted) — passes.
3. **Synth** produces a prompt: _"Event: pod `argocd-server-7d-9xk2` in `argocd` is in BackOff (reason: failed container). Investigate the root cause. Current permission mode: `auto-safe`. If a whitelisted remediation applies, you may execute it; otherwise propose."_
4. **Agent invoked** — runs `kubectl describe`, fetches `--previous` logs, dispatches `log_investigator` subagent, consults memory (finds a similar OOM incident from 3 days ago).
5. Agent issues `kubectl delete pod argocd-server-7d-9xk2`. **PreToolUse hook** verifies: verb in safe allowlist? yes. Mode `auto-safe`? yes. → permitted. Pod deletes, deployment recreates it.
6. **Outcome** posts to Telegram: _"✅ argocd-server-7d-9xk2 crashlooped (OOM, similar to 2026-05-22). Deleted pod; new pod healthy after 12s. Journal: incident-0147."_ Records 30-min cooldown on `argocd-server`.
7. If BackOff fires again immediately, the filter drops it. After the cooldown, if it keeps firing, the agent gets re-invoked and **memory tells it "I just tried the simple fix and it didn't stick"** — it escalates (deeper investigation, or "this needs a human" Telegram message).

### Why one event = one fresh agent invocation (not a long session)

This is the most important design choice in autonomous mode:

- **No accumulating context** — each investigation starts clean; no chance of cross-contaminating unrelated incidents.
- **Easy to upgrade** — deploy a new agent version between events; no in-flight state to migrate.
- **Crash-safe** — if the agent crashes mid-investigation, we lose one event, not the whole sentinel. Watcher just keeps streaming.
- **Memory lives in the journal, not in the session** — "we've seen this before" is implemented by _reading_ journal entries at the start of each fresh invocation, not by a long-lived chat.

The sentinel _process_ is long-running; every agent _call_ is a one-shot.

### Layered safety (three independent defences)

1. **Filter layer** (before agent): severity, dedup, cooldown, rate limit, namespace allowlist. Rule-based, deterministic.
2. **Hook layer** (during agent): PreToolUse hook checks every mutating command against the safe-action allowlist.
3. **Permission mode** (agent policy): `readonly` / `propose` / `auto-safe`. Single flag, easy to flip in an emergency.

---

## Cross-cutting Concerns

### Config & secrets

- Auth: defaults to whatever the local `claude` CLI is logged into (OAuth via Claude Code). Set `ANTHROPIC_API_KEY` in env (or `.env`) to override and bill against the Anthropic API directly.
- Kubeconfig: default `$KUBECONFIG` / `~/.kube/config`. Single cluster.
- Per-user config at `~/.k8sense/config.toml`:
  ```toml
  permission_mode = "readonly"   # readonly | propose | auto-safe
  allowed_namespaces = ["argocd", "longhorn"]
  rate_limit_per_hour = 20
  ```

### Observability

- Every tool call + result logged to `~/.k8sense/logs/<date>.jsonl`. Indispensable in Phase 4 for verifying hook behaviour.
- Phase 5 exposes a Prometheus-style `/metrics` endpoint so the sentinel can be scraped by the very stack it monitors.

### Dependencies (deliberately small)

- `claude-agent-sdk` — the SDK
- `rich` — CLI rendering
- `pydantic` — tool schemas (Phase 3+)
- `pyyaml` — parsing kubectl YAML
- `python-dotenv` — dev only
- `pytest`, `pytest-asyncio` — tests

### Things explicitly NOT in scope

- Web UI.
- Multi-cluster support.
- Prometheus integration in Phase 1 (added in Phase 2 only if needed by an eval question).
- Cluster repair beyond the hook-gated safe-action allowlist.
- Cross-agent network communication. Subagents are in-process only.
- Long-lived agent sessions. One-shot per invocation, period.

---

## Open decisions (deferred to per-phase implementation plans)

These don't need resolution in this spec — they're noted so the per-phase plans pick them up:

- Exact Telegram delivery path (existing plugin via Claude Code vs. direct bot API call from the sentinel process). Decide in Phase 5.
- Whether to add a Prometheus query tool in Phase 2 or defer until Phase 4/5.
- Whether to ship `k8sense doctor` (env check) in Phase 1 or Phase 2.

## Success criteria

- **Phase 1:** Asking `k8sense ask "why is pod X restarting?"` produces a coherent, accurate investigation in under 60s. Eval suite green.
- **Phase 5:** Sentinel runs for 7 consecutive days against `homelab-k3s` without false-positive remediations (defined as: a `kubectl delete pod` action that did not correspond to an actually-broken pod). Telegram messages are readable and actionable.

## Reference (existing context)

- `~/codes/homelab-k8s/` — your existing k3s manifests and applications.
- `~/codes/homelab-k8s/k8s-event-watcher/` — existing event watcher to consider reusing in the Phase 5 watcher component.
- `~/codes/homelab-scripts/monitor/` — existing monitor scripts.
- `~/codes/homelab-scripts/k3s/` — k3s-specific tooling.
