# k8sense Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `k8sense ask "<question>"` — a one-shot CLI that investigates the local k3s cluster using the Claude Agent SDK and a single read-only `kubectl` tool, streams its reasoning with `rich`, and is exercised by an eval harness from day one.

**Architecture:** A Python package `k8sense` with a thin CLI (`cli.py`) wrapping a core agent loop (`agent.py`). The agent has exactly one tool — `kubectl` — with a verb allowlist enforced inside the tool itself (hooks come in Phase 4). A system prompt with a cached cluster topology snapshot frames the agent as a homelab k3s SRE. Streaming output renders via `rich`. An eval harness (`evals/`) runs ~10 fingerprint-checked questions before each merge.

**Tech Stack:**

- Python 3.11+
- `claude-agent-sdk` (the SDK)
- `rich` (CLI rendering)
- `pytest`, `pytest-asyncio` (tests)
- `python-dotenv` (dev env loading)
- The user's existing `homelab-k3s` cluster (context: `homelab-k3s` in `~/.kube/config`)

**Spec:** `docs/superpowers/specs/2026-05-25-k8sense-design.md`

**Testing discipline:** Strict red-green-refactor for all deterministic units (per the spec's Testing Strategy section). No mocking of `kubectl` or the SDK. Allowlist-rejection paths run without a cluster; success paths run against `kube-system` read-only.

**Deferred to Phase 2:** the spec calls for "recorded SDK transcripts replayed in CI" as the integration layer. Implementing that cleanly requires HTTP-level recording (VCR-style) since the SDK doesn't expose a replay mode. For Phase 1 we get equivalent coverage from the eval runner (Task 11) + the smoke test (Task 12). Fixture recording is added in Phase 2 once there's more behaviour worth pinning.

---

## File Structure (final state at end of Phase 1)

```
new-project/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── pytest.ini
├── src/k8sense/
│   ├── __init__.py
│   ├── cli.py                   # argparse entrypoint: ask / doctor
│   ├── agent.py                 # core agent loop (SDK glue)
│   ├── render.py                # rich-based output rendering
│   ├── prompts/
│   │   ├── __init__.py
│   │   └── system.py            # system prompt + topology snapshot
│   └── tools/
│       ├── __init__.py
│       └── kubectl.py           # the single Phase-1 tool
├── evals/
│   ├── __init__.py
│   ├── dataset.jsonl            # 10 cluster questions + fingerprints
│   └── runner.py                # fingerprint scorer
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── unit/
    │   ├── __init__.py
    │   ├── test_kubectl_allowlist.py
    │   ├── test_kubectl_execution.py
    │   ├── test_system_prompt.py
    │   ├── test_render.py
    │   ├── test_agent_helpers.py
    │   └── test_eval_runner.py
    ├── integration/
    │   ├── __init__.py
    │   └── test_ask_end_to_end.py
    └── smoke/
        ├── __init__.py
        └── test_real_cluster.py
```

**Responsibility per file:**

- `cli.py` — argument parsing, exit codes, dispatch to `agent.run_ask()` or `agent.run_doctor()`. No business logic.
- `agent.py` — assembles `ClaudeAgentOptions`, owns the SDK client, dispatches streamed messages to the renderer, enforces `MAX_TOOL_CALLS`.
- `render.py` — `Renderer` class with pure formatting methods that write to a `rich.console.Console`.
- `prompts/system.py` — builds the system prompt and fetches the topology snapshot at startup.
- `tools/kubectl.py` — `is_allowed`, `run_kubectl`, and the `@tool`-decorated `kubectl_tool` wrapper. The plain Python function is testable in isolation; the `@tool` wrapper is exercised via integration.
- `evals/dataset.jsonl` — one JSON per line: `{id, question, fingerprints: [{type, value}]}`.
- `evals/runner.py` — loads dataset, runs the agent for each question, scores fingerprints, emits a markdown report.

---

## Task 1: Project skeleton

**Files:**

- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `pytest.ini`
- Create: `README.md`
- Create: `src/k8sense/__init__.py`
- Create: `src/k8sense/prompts/__init__.py`
- Create: `src/k8sense/tools/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/smoke/__init__.py`
- Create: `evals/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "k8sense"
version = "0.1.0"
description = "Claude Agent SDK SRE for homelab k3s clusters"
requires-python = ">=3.11"
dependencies = [
    "claude-agent-sdk>=0.1.0",
    "rich>=13.7",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[project.scripts]
k8sense = "k8sense.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.env
.pytest_cache/
.coverage
dist/
build/
.k8sense/
```

- [ ] **Step 3: Create `.env.example`**

```
ANTHROPIC_API_KEY=sk-ant-...
# Optional: override default model
# K8SENSE_MODEL=claude-sonnet-4-6
```

- [ ] **Step 4: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
markers =
    smoke: real cluster smoke tests (not run by default)
addopts = -ra --strict-markers
```

- [ ] **Step 5: Create `README.md`**

````markdown
# k8sense

A Claude Agent SDK SRE for homelab k3s clusters.

## Phase 1 status

`k8sense ask "<question>"` — one-shot investigation CLI.

## Install (dev)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # then add your ANTHROPIC_API_KEY
```
````

## Usage

```bash
k8sense doctor                                  # check environment
k8sense ask "list all namespaces"               # ask a question
```

See `docs/superpowers/specs/2026-05-25-k8sense-design.md` for the full design.

````

- [ ] **Step 6: Create empty package `__init__.py` files**

For each of these files, create them with content `"""k8sense package."""` (or empty for the test/eval dirs):
- `src/k8sense/__init__.py` → `"""k8sense — homelab k3s SRE agent."""\n__version__ = "0.1.0"\n`
- `src/k8sense/prompts/__init__.py` → empty
- `src/k8sense/tools/__init__.py` → empty
- `tests/__init__.py` → empty
- `tests/unit/__init__.py` → empty
- `tests/integration/__init__.py` → empty
- `tests/smoke/__init__.py` → empty
- `evals/__init__.py` → empty

- [ ] **Step 7: Create `tests/conftest.py`**

```python
"""Shared test fixtures."""
import os
import pytest


@pytest.fixture(autouse=True)
def _no_real_api_calls(monkeypatch):
    """Guard unit tests from accidentally hitting the live API.

    Integration tests explicitly opt in by setting K8SENSE_ALLOW_API=1 in
    their own setup. Unit tests must never touch the network.
    """
    if not os.environ.get("K8SENSE_ALLOW_API"):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
````

- [ ] **Step 8: Verify it installs**

Run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest --collect-only
```

Expected: `pip install` succeeds; `pytest --collect-only` shows 0 tests (no test files yet) and no errors.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .gitignore .env.example pytest.ini README.md \
    src/k8sense/__init__.py src/k8sense/prompts/__init__.py \
    src/k8sense/tools/__init__.py \
    tests/__init__.py tests/conftest.py \
    tests/unit/__init__.py tests/integration/__init__.py tests/smoke/__init__.py \
    evals/__init__.py
git commit -m "Scaffold k8sense package and test layout"
```

---

## Task 2: kubectl allowlist (pure logic — strict TDD)

The allowlist is the smallest, purest unit in the system. We TDD it first so the discipline is established before anything touches the SDK or a cluster.

**Files:**

- Create: `tests/unit/test_kubectl_allowlist.py`
- Create: `src/k8sense/tools/kubectl.py`

- [ ] **Step 1: Write the failing test for allowed verbs**

Create `tests/unit/test_kubectl_allowlist.py`:

```python
"""Allowlist rules for the kubectl tool."""
import pytest

from k8sense.tools.kubectl import is_allowed


@pytest.mark.parametrize("verb", ["get", "describe", "logs", "top", "events", "version"])
def test_allowed_verbs_pass(verb):
    assert is_allowed([verb, "pods"]) is True


@pytest.mark.parametrize("verb", ["delete", "apply", "create", "scale", "patch", "edit", "exec", "rollout"])
def test_mutating_verbs_rejected(verb):
    assert is_allowed([verb, "pod", "x"]) is False


def test_empty_args_rejected():
    assert is_allowed([]) is False


def test_unknown_verb_rejected():
    assert is_allowed(["frobnicate"]) is False
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
pytest tests/unit/test_kubectl_allowlist.py -v
```

Expected: All tests fail with `ImportError: cannot import name 'is_allowed' from 'k8sense.tools.kubectl'` (the module doesn't exist yet).

- [ ] **Step 3: Write the minimal implementation**

Create `src/k8sense/tools/kubectl.py`:

```python
"""kubectl tool: read-only verb allowlist + subprocess wrapper."""
from __future__ import annotations

ALLOWED_VERBS: frozenset[str] = frozenset(
    {"get", "describe", "logs", "top", "events", "version"}
)


def is_allowed(args: list[str]) -> bool:
    """Return True if the first positional arg is an allowed read-only verb."""
    if not args:
        return False
    return args[0] in ALLOWED_VERBS
```

- [ ] **Step 4: Run the test — expect pass**

Run:

```bash
pytest tests/unit/test_kubectl_allowlist.py -v
```

Expected: All 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_kubectl_allowlist.py src/k8sense/tools/kubectl.py
git commit -m "Add kubectl verb allowlist with tests"
```

---

## Task 3: kubectl execution (subprocess + timeout — strict TDD)

We add the subprocess-driven execution layer. Allowlist rejection paths can be tested without a cluster; success paths run a known-safe command (`kubectl version --client`) which doesn't need cluster connectivity.

**Files:**

- Modify: `src/k8sense/tools/kubectl.py`
- Create: `tests/unit/test_kubectl_execution.py`

- [ ] **Step 1: Write the failing test for rejection path**

Create `tests/unit/test_kubectl_execution.py`:

```python
"""Behaviour of run_kubectl: allowlist rejection, success, timeout."""
import asyncio
import shutil

import pytest

from k8sense.tools.kubectl import run_kubectl


@pytest.mark.asyncio
async def test_disallowed_verb_returns_error_without_running_subprocess():
    result = await run_kubectl(["delete", "pod", "x"])
    assert result["exit_code"] == -1
    assert "not allowed" in result["stderr"]
    assert result["stdout"] == ""


@pytest.mark.asyncio
async def test_empty_args_returns_error():
    result = await run_kubectl([])
    assert result["exit_code"] == -1
    assert "not allowed" in result["stderr"]


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.asyncio
async def test_version_client_succeeds_without_cluster():
    result = await run_kubectl(["version", "--client", "-o", "yaml"])
    assert result["exit_code"] == 0
    assert "clientVersion" in result["stdout"]


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.asyncio
async def test_timeout_returns_error():
    # Force timeout by setting an absurdly small budget on a real-but-slow call.
    # `kubectl version --client` exits in <1s normally; 0.001s ensures timeout.
    result = await run_kubectl(["version", "--client"], timeout=0.001)
    assert result["exit_code"] == -1
    assert "timeout" in result["stderr"].lower()
```

- [ ] **Step 2: Run the tests — expect failure**

Run:

```bash
pytest tests/unit/test_kubectl_execution.py -v
```

Expected: All tests fail with `ImportError: cannot import name 'run_kubectl' from 'k8sense.tools.kubectl'`.

- [ ] **Step 3: Extend `src/k8sense/tools/kubectl.py`**

Replace the file with:

```python
"""kubectl tool: read-only verb allowlist + subprocess wrapper."""
from __future__ import annotations

import asyncio
from typing import Any

ALLOWED_VERBS: frozenset[str] = frozenset(
    {"get", "describe", "logs", "top", "events", "version"}
)

DEFAULT_TIMEOUT_S: float = 15.0


def is_allowed(args: list[str]) -> bool:
    """Return True if the first positional arg is an allowed read-only verb."""
    if not args:
        return False
    return args[0] in ALLOWED_VERBS


async def run_kubectl(
    args: list[str],
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute kubectl with the given args. Return {stdout, stderr, exit_code}.

    Allowlist is enforced before subprocess is spawned. On timeout the process
    is killed and exit_code is -1.
    """
    if not is_allowed(args):
        verb = args[0] if args else "<empty>"
        return {
            "stdout": "",
            "stderr": f"verb '{verb}' not allowed in read-only mode",
            "exit_code": -1,
        }

    proc = await asyncio.create_subprocess_exec(
        "kubectl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
            "exit_code": -1,
        }

    return {
        "stdout": stdout_b.decode("utf-8", errors="replace"),
        "stderr": stderr_b.decode("utf-8", errors="replace"),
        "exit_code": proc.returncode if proc.returncode is not None else -1,
    }
```

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
pytest tests/unit/test_kubectl_execution.py -v
```

Expected: All 4 tests pass (timeout test passes because `0.001s` is below normal startup).

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_kubectl_execution.py src/k8sense/tools/kubectl.py
git commit -m "Add run_kubectl subprocess wrapper with timeout"
```

---

## Task 4: kubectl tool wrapper for the SDK

Wrap `run_kubectl` with the SDK's `@tool` decorator so the agent can call it. This is integration code — the wrapper itself is short, and behaviour is verified by the integration test later.

**Files:**

- Modify: `src/k8sense/tools/kubectl.py`
- Create: `tests/unit/test_kubectl_tool_wrapper.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_kubectl_tool_wrapper.py`:

```python
"""The @tool-decorated wrapper turns run_kubectl's dict into SDK content."""
import pytest

from k8sense.tools.kubectl import kubectl_tool


@pytest.mark.asyncio
async def test_wrapper_returns_sdk_content_block_for_rejected_verb():
    result = await kubectl_tool({"args": ["delete", "pod", "x"]})
    assert "content" in result
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block["type"] == "text"
    assert "not allowed" in block["text"]
    assert "exit_code=-1" in block["text"]
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
pytest tests/unit/test_kubectl_tool_wrapper.py -v
```

Expected: Fails with `ImportError: cannot import name 'kubectl_tool'`.

- [ ] **Step 3: Add the wrapper to `src/k8sense/tools/kubectl.py`**

Append to the bottom of the file:

```python


# --- SDK wrapper ---------------------------------------------------------

from claude_agent_sdk import tool  # noqa: E402


@tool(
    "kubectl",
    "Run a READ-ONLY kubectl command against the homelab-k3s cluster. "
    "Allowed verbs: get, describe, logs, top, events, version. "
    "Returns stdout, stderr, and exit_code.",
    {"args": list[str]},
)
async def kubectl_tool(input_data: dict[str, Any]) -> dict[str, Any]:
    args = input_data.get("args") or []
    result = await run_kubectl(args)
    text = (
        f"$ kubectl {' '.join(args) if args else '<no args>'}\n"
        f"exit_code={result['exit_code']}\n"
        f"--- stdout ---\n{result['stdout']}\n"
        f"--- stderr ---\n{result['stderr']}"
    )
    return {"content": [{"type": "text", "text": text}]}
```

> **Note on the `@tool` API:** if `claude_agent_sdk.tool` exposes a slightly different signature in the installed SDK version (e.g. it expects a Pydantic model, or the schema dict uses a different shape), adjust to match. The contract this code expects: a callable decorator that wraps an async function and registers it with `create_sdk_mcp_server` later. If the import fails, run `python -c "import claude_agent_sdk; print(dir(claude_agent_sdk))"` to inspect the real API surface before improvising.

- [ ] **Step 4: Run the test — expect pass**

Run:

```bash
pytest tests/unit/test_kubectl_tool_wrapper.py -v
```

Expected: 1 test passes.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_kubectl_tool_wrapper.py src/k8sense/tools/kubectl.py
git commit -m "Add @tool-decorated kubectl wrapper for the SDK"
```

---

## Task 5: System prompt + topology snapshot

**Files:**

- Create: `src/k8sense/prompts/system.py`
- Create: `tests/unit/test_system_prompt.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_system_prompt.py`:

```python
"""System prompt assembly: template + topology snapshot injection."""
import pytest

from k8sense.prompts.system import build_system_prompt_from_topology


def test_prompt_includes_homelab_framing():
    prompt = build_system_prompt_from_topology("NAMESPACE\nargocd\nlonghorn\n")
    assert "k8sense" in prompt.lower()
    assert "homelab" in prompt.lower()
    assert "SRE" in prompt or "sre" in prompt.lower()


def test_prompt_includes_topology_snapshot():
    topology = "NAMESPACE   STATUS   AGE\nargocd      Active   1d\n"
    prompt = build_system_prompt_from_topology(topology)
    assert topology in prompt


def test_prompt_instructs_investigation_before_concluding():
    prompt = build_system_prompt_from_topology("")
    assert "investigate" in prompt.lower()


def test_prompt_lists_allowed_kubectl_verbs():
    prompt = build_system_prompt_from_topology("")
    # All read-only verbs should be mentioned so the model knows what's available
    for verb in ["get", "describe", "logs", "top", "events"]:
        assert verb in prompt
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
pytest tests/unit/test_system_prompt.py -v
```

Expected: `ImportError: No module named 'k8sense.prompts.system'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/k8sense/prompts/system.py`:

```python
"""System prompt assembly for the k8sense agent."""
from __future__ import annotations

from k8sense.tools.kubectl import run_kubectl

_TEMPLATE = """You are k8sense, a careful and methodical SRE for the homelab-k3s Kubernetes cluster.

Your job is to investigate questions about the cluster by running read-only kubectl commands through the `kubectl` tool, then synthesise a clear explanation in plain English.

You have exactly one tool: `kubectl`. It accepts a list of arguments. Allowed verbs: get, describe, logs, top, events, version. Mutating verbs are rejected.

Conventions:
- Always investigate before concluding. Run at least one kubectl call unless the question is purely conceptual.
- Use namespaces, pod names, and resource kinds from the topology snapshot below.
- Prefer specific invocations (e.g. `kubectl describe pod X -n Y`) over broad sweeps.
- If a tool call fails, read the stderr and either retry with adjusted args or explain why you cannot continue.
- Be concise in your final answer. Prefer bullet points for multi-part findings.

Cluster topology snapshot (captured at startup):
{topology}
"""


def build_system_prompt_from_topology(topology: str) -> str:
    """Pure assembly: takes a topology string, returns the rendered prompt."""
    return _TEMPLATE.format(topology=topology or "(snapshot unavailable)")


async def build_system_prompt() -> str:
    """Fetch the topology snapshot from the live cluster and assemble the prompt.

    Raises RuntimeError if the cluster is unreachable.
    """
    result = await run_kubectl(["get", "ns,nodes", "-o", "wide"])
    if result["exit_code"] != 0:
        raise RuntimeError(
            f"cluster unreachable (kubectl exit {result['exit_code']}): {result['stderr']}"
        )
    return build_system_prompt_from_topology(result["stdout"])
```

- [ ] **Step 4: Run the test — expect pass**

Run:

```bash
pytest tests/unit/test_system_prompt.py -v
```

Expected: All 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_system_prompt.py src/k8sense/prompts/system.py
git commit -m "Add system prompt builder with topology injection"
```

---

## Task 6: Renderer (rich-based — strict TDD via captured console)

The renderer is pure formatting. We use `rich.console.Console` with `record=True` to capture output and assert against it without ANSI parsing.

**Files:**

- Create: `src/k8sense/render.py`
- Create: `tests/unit/test_render.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_render.py`:

```python
"""Renderer formatting: thinking, tool_call, final, error."""
import pytest
from rich.console import Console

from k8sense.render import Renderer


@pytest.fixture
def captured():
    console = Console(record=True, force_terminal=False, width=120)
    return Renderer(console=console), console


def test_thinking_writes_to_console(captured):
    renderer, console = captured
    renderer.thinking("considering the next step")
    output = console.export_text()
    assert "considering the next step" in output


def test_tool_call_includes_command_and_args(captured):
    renderer, console = captured
    renderer.tool_call("kubectl", {"args": ["get", "pods", "-n", "argocd"]})
    output = console.export_text()
    assert "kubectl" in output
    assert "get pods -n argocd" in output


def test_tool_result_includes_truncated_stdout(captured):
    renderer, console = captured
    long_stdout = "line\n" * 200
    renderer.tool_result(stdout=long_stdout, stderr="", exit_code=0)
    output = console.export_text()
    assert "exit_code=0" in output
    # Should truncate; not print 200 lines verbatim
    line_count = output.count("\n")
    assert line_count < 60, f"expected truncated output, got {line_count} lines"


def test_final_prints_answer(captured):
    renderer, console = captured
    renderer.final("The cluster has 3 nodes and 12 namespaces.")
    output = console.export_text()
    assert "3 nodes" in output
    assert "12 namespaces" in output


def test_error_prints_message(captured):
    renderer, console = captured
    renderer.error("max tool calls exceeded")
    output = console.export_text()
    assert "max tool calls exceeded" in output
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
pytest tests/unit/test_render.py -v
```

Expected: `ImportError: No module named 'k8sense.render'`.

- [ ] **Step 3: Write the implementation**

Create `src/k8sense/render.py`:

```python
"""rich-based renderer for streamed agent output."""
from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

_STDOUT_TRUNCATE_LINES = 30
_STDOUT_TRUNCATE_CHARS = 2000


class Renderer:
    """Streams agent activity to a rich Console with a consistent visual language."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def thinking(self, text: str) -> None:
        self.console.print(Text(text, style="dim"))

    def tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        # Render kubectl args inline for readability; other tools fall back to repr.
        if name == "kubectl":
            args = arguments.get("args") or []
            command = "kubectl " + " ".join(args)
        else:
            command = f"{name}({arguments!r})"
        self.console.print(Text(f"→ {command}", style="cyan"))

    def tool_result(self, stdout: str, stderr: str, exit_code: int) -> None:
        body_parts = [f"exit_code={exit_code}"]
        if stdout:
            body_parts.append(_truncate(stdout, label="stdout"))
        if stderr:
            body_parts.append(_truncate(stderr, label="stderr"))
        body = "\n".join(body_parts)
        style = "green" if exit_code == 0 else "yellow"
        self.console.print(Panel(body, border_style=style, padding=(0, 1)))

    def final(self, text: str) -> None:
        self.console.print(Panel(Markdown(text), border_style="bold blue", padding=(0, 1)))

    def error(self, message: str) -> None:
        self.console.print(Text(f"✗ {message}", style="bold red"))


def _truncate(text: str, label: str) -> str:
    lines = text.splitlines()
    truncated = False
    if len(lines) > _STDOUT_TRUNCATE_LINES:
        lines = lines[:_STDOUT_TRUNCATE_LINES]
        truncated = True
    joined = "\n".join(lines)
    if len(joined) > _STDOUT_TRUNCATE_CHARS:
        joined = joined[:_STDOUT_TRUNCATE_CHARS]
        truncated = True
    suffix = "\n… (truncated)" if truncated else ""
    return f"--- {label} ---\n{joined}{suffix}"
```

- [ ] **Step 4: Run the test — expect pass**

Run:

```bash
pytest tests/unit/test_render.py -v
```

Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_render.py src/k8sense/render.py
git commit -m "Add rich-based Renderer for streamed agent output"
```

---

## Task 7: Agent helpers (testable parts of the agent loop)

The agent module has two halves: pure orchestration helpers (testable in isolation) and the actual SDK call (exercised by the integration test). We TDD the helpers first.

**Files:**

- Create: `src/k8sense/agent.py`
- Create: `tests/unit/test_agent_helpers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_agent_helpers.py`:

```python
"""Pure helpers in agent.py: options builder, tool-call counter, message dispatch."""
import pytest

from k8sense.agent import (
    MAX_TOOL_CALLS,
    ToolBudget,
    build_options,
)


def test_build_options_returns_object_with_system_prompt():
    options = build_options("SYS PROMPT", model_id="claude-sonnet-4-6")
    assert getattr(options, "system_prompt", None) == "SYS PROMPT"


def test_build_options_allows_only_the_kubectl_tool():
    options = build_options("SYS", model_id="claude-sonnet-4-6")
    allowed = getattr(options, "allowed_tools", None)
    assert allowed is not None
    assert any("kubectl" in t for t in allowed)
    # No other tools should slip in
    assert len(allowed) == 1


def test_tool_budget_allows_calls_under_limit():
    budget = ToolBudget(limit=3)
    assert budget.charge() is True
    assert budget.charge() is True
    assert budget.charge() is True


def test_tool_budget_rejects_after_limit():
    budget = ToolBudget(limit=2)
    budget.charge()
    budget.charge()
    assert budget.charge() is False


def test_default_tool_budget_matches_max_tool_calls():
    budget = ToolBudget()
    assert budget.limit == MAX_TOOL_CALLS
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
pytest tests/unit/test_agent_helpers.py -v
```

Expected: `ImportError: cannot import name ... from 'k8sense.agent'`.

- [ ] **Step 3: Write the implementation**

Create `src/k8sense/agent.py`:

```python
"""Core agent loop: assembles SDK options, drives the streaming receive loop."""
from __future__ import annotations

import os
from dataclasses import dataclass

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    create_sdk_mcp_server,
)

from k8sense.prompts.system import build_system_prompt
from k8sense.render import Renderer
from k8sense.tools.kubectl import kubectl_tool

MAX_TOOL_CALLS = 20
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass
class ToolBudget:
    """Soft cap on tool calls per agent invocation."""

    limit: int = MAX_TOOL_CALLS
    used: int = 0

    def charge(self) -> bool:
        """Return True if a tool call is permitted, False if the budget is exhausted."""
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


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


async def run_ask(question: str, renderer: Renderer, model_id: str | None = None) -> int:
    """Run a one-shot investigation. Returns the process exit code."""
    try:
        system_prompt = await build_system_prompt()
    except RuntimeError as exc:
        renderer.error(str(exc))
        return 1

    options = build_options(system_prompt, model_id=model_id or os.environ.get("K8SENSE_MODEL", DEFAULT_MODEL))
    budget = ToolBudget()

    async with ClaudeSDKClient(options=options) as client:
        await client.query(question)
        async for message in client.receive_response():
            should_continue = _dispatch_message(message, renderer, budget)
            if not should_continue:
                return 1
    return 0


def _dispatch_message(message, renderer: Renderer, budget: ToolBudget) -> bool:
    """Route a streamed message to the renderer. Return False to abort the loop."""
    # We import block types lazily so that the unit-test suite doesn't choke on a
    # missing optional symbol; the real types live in claude_agent_sdk.
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        UserMessage,
    )

    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                renderer.thinking(block.text)
            elif isinstance(block, ToolUseBlock):
                if not budget.charge():
                    renderer.error(f"hit max tool calls ({budget.limit})")
                    return False
                renderer.tool_call(block.name, block.input)
        return True

    if isinstance(message, UserMessage):
        # Tool results come back as UserMessage content. Render the result panel.
        for block in getattr(message, "content", []) or []:
            content_block = getattr(block, "content", None)
            if content_block is None:
                continue
            text = _extract_tool_result_text(content_block)
            renderer.tool_result(stdout=text, stderr="", exit_code=0)
        return True

    if isinstance(message, ResultMessage):
        final_text = getattr(message, "result", None) or ""
        if final_text:
            renderer.final(final_text)
        return True

    return True


def _extract_tool_result_text(content) -> str:
    """Tool results can arrive as a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts)
    return str(content)
```

> **Note on SDK message types:** the names and locations above (`AssistantMessage`, `UserMessage`, `ResultMessage`, `TextBlock`, `ToolUseBlock`) match the public `claude_agent_sdk` API at time of writing. If `from claude_agent_sdk import X` fails for any of these, run `python -c "import claude_agent_sdk; print(sorted(n for n in dir(claude_agent_sdk) if not n.startswith('_')))"` and adjust imports before fixing the test.

- [ ] **Step 4: Run the helper tests — expect pass**

Run:

```bash
pytest tests/unit/test_agent_helpers.py -v
```

Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_agent_helpers.py src/k8sense/agent.py
git commit -m "Add agent loop with ToolBudget and options builder"
```

---

## Task 8: CLI entrypoint

`argparse` with two subcommands: `ask` (Phase 1) and `doctor` (env check).

**Files:**

- Create: `src/k8sense/cli.py`
- Create: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli.py`:

```python
"""CLI argument parsing and dispatch."""
import pytest

from k8sense.cli import build_parser, doctor_check


def test_parser_accepts_ask_subcommand():
    parser = build_parser()
    ns = parser.parse_args(["ask", "why is pod X crashing?"])
    assert ns.command == "ask"
    assert ns.question == "why is pod X crashing?"


def test_parser_accepts_doctor_subcommand():
    parser = build_parser()
    ns = parser.parse_args(["doctor"])
    assert ns.command == "doctor"


def test_parser_rejects_unknown_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["frobnicate"])


def test_doctor_check_reports_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    findings = doctor_check()
    assert any("ANTHROPIC_API_KEY" in f.message and not f.ok for f in findings)


def test_doctor_check_reports_kubectl_presence(monkeypatch):
    findings = doctor_check()
    # Either present or absent — both produce a finding
    assert any("kubectl" in f.message for f in findings)
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
pytest tests/unit/test_cli.py -v
```

Expected: `ImportError: cannot import name 'build_parser' from 'k8sense.cli'`.

- [ ] **Step 3: Write the implementation**

Create `src/k8sense/cli.py`:

```python
"""k8sense CLI: argument parsing, doctor env check, dispatch to the agent."""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

from k8sense.agent import run_ask
from k8sense.render import Renderer


@dataclass
class Finding:
    ok: bool
    message: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="k8sense", description="Homelab k3s SRE agent")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="ask a question about the cluster")
    ask.add_argument("question", help="natural-language question, e.g. 'why is pod X crashing?'")

    sub.add_parser("doctor", help="check the local environment")

    return parser


def doctor_check() -> list[Finding]:
    findings: list[Finding] = []

    if shutil.which("kubectl"):
        findings.append(Finding(ok=True, message="kubectl is on PATH"))
    else:
        findings.append(Finding(ok=False, message="kubectl not found on PATH"))

    kubeconfig = os.environ.get("KUBECONFIG") or os.path.expanduser("~/.kube/config")
    if os.path.exists(kubeconfig):
        findings.append(Finding(ok=True, message=f"kubeconfig found at {kubeconfig}"))
    else:
        findings.append(Finding(ok=False, message=f"kubeconfig not found at {kubeconfig}"))

    if os.environ.get("ANTHROPIC_API_KEY"):
        findings.append(Finding(ok=True, message="ANTHROPIC_API_KEY is set"))
    else:
        findings.append(Finding(ok=False, message="ANTHROPIC_API_KEY is not set"))

    return findings


def _print_findings(findings: list[Finding], renderer: Renderer) -> int:
    failed = 0
    for f in findings:
        prefix = "✓" if f.ok else "✗"
        if f.ok:
            renderer.console.print(f"[green]{prefix}[/green] {f.message}")
        else:
            renderer.console.print(f"[red]{prefix}[/red] {f.message}")
            failed += 1
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    ns = parser.parse_args(argv)
    renderer = Renderer()

    if ns.command == "doctor":
        findings = doctor_check()
        return _print_findings(findings, renderer)

    if ns.command == "ask":
        return asyncio.run(run_ask(ns.question, renderer))

    parser.error(f"unknown command: {ns.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test — expect pass**

Run:

```bash
pytest tests/unit/test_cli.py -v
```

Expected: All 5 tests pass.

- [ ] **Step 5: Manual smoke of the doctor subcommand**

Run:

```bash
k8sense doctor
```

Expected: prints three findings (kubectl, kubeconfig, ANTHROPIC_API_KEY). Exits 0 if all green, 1 otherwise.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_cli.py src/k8sense/cli.py
git commit -m "Add k8sense CLI with ask and doctor subcommands"
```

---

## Task 9: Eval fingerprint scorer (strict TDD — pure logic)

The fingerprint matcher is the second piece of pure logic we TDD. Three fingerprint types: substring, regex, structural (tool-name presence).

**Files:**

- Create: `evals/runner.py`
- Create: `tests/unit/test_eval_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_eval_runner.py`:

```python
"""Fingerprint scoring for eval results."""
import pytest

from evals.runner import EvalCase, EvalResult, score_fingerprints


def _result(final_text: str = "", tool_calls: list[dict] | None = None) -> EvalResult:
    return EvalResult(final_text=final_text, tool_calls=tool_calls or [])


def test_substring_match_passes():
    case = EvalCase(id="t1", question="?", fingerprints=[
        {"type": "substring", "value": "argocd"},
    ])
    result = _result(final_text="argocd is healthy")
    passes, failures = score_fingerprints(case, result)
    assert passes is True
    assert failures == []


def test_substring_match_fails_when_missing():
    case = EvalCase(id="t2", question="?", fingerprints=[
        {"type": "substring", "value": "argocd"},
    ])
    result = _result(final_text="no relevant content")
    passes, failures = score_fingerprints(case, result)
    assert passes is False
    assert "substring 'argocd'" in failures[0]


def test_regex_match_passes():
    case = EvalCase(id="t3", question="?", fingerprints=[
        {"type": "regex", "value": r"v\d+\.\d+\.\d+"},
    ])
    result = _result(final_text="cluster version v1.29.3 detected")
    passes, _ = score_fingerprints(case, result)
    assert passes is True


def test_structural_tool_name_passes():
    case = EvalCase(id="t4", question="?", fingerprints=[
        {"type": "tool_called", "value": "kubectl"},
    ])
    result = _result(tool_calls=[{"name": "kubectl", "input": {"args": ["get", "pods"]}}])
    passes, _ = score_fingerprints(case, result)
    assert passes is True


def test_structural_tool_call_args_contains():
    case = EvalCase(id="t5", question="?", fingerprints=[
        {"type": "tool_args_contains", "value": "argocd"},
    ])
    result = _result(tool_calls=[{"name": "kubectl", "input": {"args": ["get", "pods", "-n", "argocd"]}}])
    passes, _ = score_fingerprints(case, result)
    assert passes is True


def test_all_fingerprints_required():
    case = EvalCase(id="t6", question="?", fingerprints=[
        {"type": "substring", "value": "argocd"},
        {"type": "substring", "value": "longhorn"},
    ])
    result = _result(final_text="argocd is healthy")  # missing longhorn
    passes, failures = score_fingerprints(case, result)
    assert passes is False
    assert len(failures) == 1
    assert "longhorn" in failures[0]
```

- [ ] **Step 2: Run the test — expect failure**

Run:

```bash
pytest tests/unit/test_eval_runner.py -v
```

Expected: `ImportError: No module named 'evals.runner'`.

- [ ] **Step 3: Write the implementation**

Create `evals/runner.py`:

```python
"""Run the k8sense agent against the eval dataset and score fingerprint matches."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalCase:
    id: str
    question: str
    fingerprints: list[dict[str, Any]]


@dataclass
class EvalResult:
    final_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def score_fingerprints(case: EvalCase, result: EvalResult) -> tuple[bool, list[str]]:
    """Return (all_pass, failure_messages)."""
    failures: list[str] = []
    for fp in case.fingerprints:
        kind = fp["type"]
        value = fp["value"]
        if kind == "substring":
            if value not in result.final_text:
                failures.append(f"substring '{value}' not found in final text")
        elif kind == "regex":
            if not re.search(value, result.final_text):
                failures.append(f"regex '{value}' did not match final text")
        elif kind == "tool_called":
            if not any(tc.get("name") == value for tc in result.tool_calls):
                failures.append(f"tool '{value}' was never called")
        elif kind == "tool_args_contains":
            ok = any(
                value in " ".join(str(a) for a in (tc.get("input") or {}).get("args", []))
                for tc in result.tool_calls
            )
            if not ok:
                failures.append(f"no tool call args contained '{value}'")
        else:
            failures.append(f"unknown fingerprint type: {kind}")
    return len(failures) == 0, failures


def load_dataset(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        data = json.loads(line)
        cases.append(EvalCase(id=data["id"], question=data["question"], fingerprints=data["fingerprints"]))
    return cases
```

- [ ] **Step 4: Run the test — expect pass**

Run:

```bash
pytest tests/unit/test_eval_runner.py -v
```

Expected: All 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_eval_runner.py evals/runner.py
git commit -m "Add eval fingerprint scorer with substring/regex/structural types"
```

---

## Task 10: Eval dataset

Ten Phase-1 cluster questions, each with at least one substring and one structural fingerprint where possible.

**Files:**

- Create: `evals/dataset.jsonl`

- [ ] **Step 1: Create `evals/dataset.jsonl`**

```jsonl
{"id": "ns-list", "question": "List every namespace in the cluster.", "fingerprints": [{"type": "tool_called", "value": "kubectl"}, {"type": "substring", "value": "kube-system"}]}
{"id": "node-count", "question": "How many nodes does the cluster have?", "fingerprints": [{"type": "tool_called", "value": "kubectl"}, {"type": "regex", "value": "\\d+\\s*nodes?"}]}
{"id": "cluster-version", "question": "What is the cluster version?", "fingerprints": [{"type": "tool_called", "value": "kubectl"}, {"type": "regex", "value": "v\\d+\\.\\d+\\.\\d+"}]}
{"id": "argocd-pods", "question": "Are the pods in the argocd namespace healthy?", "fingerprints": [{"type": "tool_args_contains", "value": "argocd"}, {"type": "substring", "value": "argocd"}]}
{"id": "restart-summary", "question": "Summarise pod restart counts across all namespaces.", "fingerprints": [{"type": "tool_called", "value": "kubectl"}, {"type": "tool_args_contains", "value": "pods"}]}
{"id": "kube-system-events", "question": "Show recent events in the kube-system namespace.", "fingerprints": [{"type": "tool_args_contains", "value": "events"}, {"type": "tool_args_contains", "value": "kube-system"}]}
{"id": "longhorn-overview", "question": "What is running in the longhorn namespace?", "fingerprints": [{"type": "tool_args_contains", "value": "longhorn"}]}
{"id": "pending-pods", "question": "Is anything stuck in Pending state right now?", "fingerprints": [{"type": "tool_called", "value": "kubectl"}, {"type": "tool_args_contains", "value": "pods"}]}
{"id": "health-summary", "question": "Give me a one-paragraph health summary of the cluster.", "fingerprints": [{"type": "tool_called", "value": "kubectl"}, {"type": "substring", "value": "node"}]}
{"id": "explain-cluster-slowness", "question": "If the cluster feels slow, what would you check first?", "fingerprints": [{"type": "tool_called", "value": "kubectl"}]}
```

- [ ] **Step 2: Verify the dataset loads**

Run:

```bash
python -c "from pathlib import Path; from evals.runner import load_dataset; cases = load_dataset(Path('evals/dataset.jsonl')); print(f'loaded {len(cases)} cases'); print([c.id for c in cases])"
```

Expected: `loaded 10 cases` followed by the list of 10 ids.

- [ ] **Step 3: Commit**

```bash
git add evals/dataset.jsonl
git commit -m "Add Phase 1 eval dataset (10 cluster questions)"
```

---

## Task 11: Eval driver — run dataset against the live agent

Wire the runner so `python -m evals.runner` executes every question and prints a markdown report. This is the "no API spend in CI" boundary: it runs locally / on-demand, not on every commit.

**Files:**

- Modify: `evals/runner.py`

- [ ] **Step 1: Append a `__main__` driver to `evals/runner.py`**

Add at the bottom of `evals/runner.py`:

```python


# --- live driver ---------------------------------------------------------

async def _run_one_case(case: EvalCase) -> EvalResult:
    """Run a single question against the real agent and capture result + tool calls."""
    from rich.console import Console

    from k8sense.agent import build_options
    from k8sense.prompts.system import build_system_prompt
    from k8sense.render import Renderer
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )

    renderer = Renderer(console=Console(file=open("/dev/null", "w")))  # silent during evals
    system_prompt = await build_system_prompt()
    options = build_options(system_prompt)
    final_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    async with ClaudeSDKClient(options=options) as client:
        await client.query(case.question)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        final_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        tool_calls.append({"name": block.name, "input": block.input})
            elif isinstance(message, ResultMessage):
                if getattr(message, "result", None):
                    final_parts.append(message.result)

    return EvalResult(final_text="\n".join(final_parts), tool_calls=tool_calls)


async def _amain() -> int:
    import argparse as _argparse

    parser = _argparse.ArgumentParser(description="Run k8sense evals")
    parser.add_argument("--dataset", default="evals/dataset.jsonl")
    parser.add_argument("--report", default="evals/report.md")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    cases = load_dataset(dataset_path)
    rows: list[str] = ["| id | pass | failures |", "| --- | --- | --- |"]
    passed = 0

    for case in cases:
        result = await _run_one_case(case)
        ok, failures = score_fingerprints(case, result)
        passed += int(ok)
        rows.append(
            f"| {case.id} | {'✓' if ok else '✗'} | {'<br>'.join(failures) if failures else ''} |"
        )

    report = [
        f"# k8sense eval report",
        "",
        f"**{passed}/{len(cases)} passed**",
        "",
        *rows,
    ]
    Path(args.report).write_text("\n".join(report) + "\n")
    print(f"{passed}/{len(cases)} passed — report written to {args.report}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    import asyncio as _asyncio
    raise SystemExit(_asyncio.run(_amain()))
```

- [ ] **Step 2: Manual run (requires API key + reachable cluster)**

Run:

```bash
python -m evals.runner
cat evals/report.md
```

Expected: a markdown table written to `evals/report.md`. Not every fingerprint must pass on the first run — the report tells you which to tighten.

> If a fingerprint is flaky for a legitimate reason (e.g. the model answers correctly but phrases it differently), loosen that fingerprint in `dataset.jsonl` — don't lower the bar by changing the scorer.

- [ ] **Step 3: Add `evals/report.md` to `.gitignore`**

Append to `.gitignore`:

```
evals/report.md
```

- [ ] **Step 4: Commit**

```bash
git add evals/runner.py .gitignore
git commit -m "Add live eval driver with markdown report"
```

---

## Task 12: End-to-end smoke test against the real cluster

One real run, marked `@pytest.mark.smoke` so it's not part of the default test suite. Verifies that the whole stack — CLI, agent, tool, SDK, cluster — works.

**Files:**

- Create: `tests/smoke/test_real_cluster.py`

- [ ] **Step 1: Create the smoke test**

Create `tests/smoke/test_real_cluster.py`:

```python
"""End-to-end smoke test: real cluster, real SDK, real model. Manual run only.

Run with:
    K8SENSE_ALLOW_API=1 pytest -m smoke -s
"""
import asyncio
import os

import pytest
from rich.console import Console

from k8sense.agent import run_ask
from k8sense.render import Renderer


@pytest.mark.smoke
def test_list_namespaces_succeeds():
    if not os.environ.get("K8SENSE_ALLOW_API"):
        pytest.skip("smoke test requires K8SENSE_ALLOW_API=1")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("smoke test requires ANTHROPIC_API_KEY")

    console = Console(record=True, force_terminal=False, width=120)
    renderer = Renderer(console=console)
    exit_code = asyncio.run(run_ask("list every namespace in the cluster", renderer))

    output = console.export_text()
    assert exit_code == 0, f"agent exited {exit_code}\n{output}"
    assert "kube-system" in output, f"expected kube-system in output:\n{output}"
```

- [ ] **Step 2: Run the smoke test manually**

Run:

```bash
K8SENSE_ALLOW_API=1 pytest -m smoke -s tests/smoke/test_real_cluster.py
```

Expected: agent investigates, prints findings, the test asserts `kube-system` is in the output. The first run may take 10-30 seconds and will charge a small amount of API usage.

- [ ] **Step 3: Commit**

```bash
git add tests/smoke/test_real_cluster.py
git commit -m "Add end-to-end smoke test for ask command"
```

---

## Task 13: README polish + final Phase 1 commit

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Replace `README.md` with the Phase-1 final version**

````markdown
# k8sense

A Claude Agent SDK-powered SRE for the homelab-k3s Kubernetes cluster.

## Status

**Phase 1 (current):** `k8sense ask "<question>"` — one-shot investigation CLI.

See [the design spec](docs/superpowers/specs/2026-05-25-k8sense-design.md) for the full 5-phase roadmap.

## Install (dev)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # then add your ANTHROPIC_API_KEY
```
````

## Usage

```bash
k8sense doctor                                       # check the environment
k8sense ask "list all namespaces"                    # ask a question
k8sense ask "is the argocd-server pod healthy?"      # investigate something specific
```

## Run the eval suite

Requires `ANTHROPIC_API_KEY` set and a reachable cluster:

```bash
python -m evals.runner
cat evals/report.md
```

## Run the test suite

```bash
pytest                                  # unit + integration (no API spend)
K8SENSE_ALLOW_API=1 pytest -m smoke     # end-to-end against real cluster (charges API)
```

## Architecture

- `src/k8sense/cli.py` — argparse entrypoint, `ask` and `doctor` subcommands.
- `src/k8sense/agent.py` — assembles `ClaudeAgentOptions`, drives the streaming receive loop.
- `src/k8sense/tools/kubectl.py` — single read-only kubectl tool with verb allowlist.
- `src/k8sense/prompts/system.py` — system prompt + cached topology snapshot.
- `src/k8sense/render.py` — rich-based output rendering.
- `evals/` — fingerprint-based eval harness.
- `tests/` — unit, integration, smoke layers.

## License

TBD (this is currently a personal learning project).

````

- [ ] **Step 2: Run the full test suite**

Run:
```bash
pytest -v
````

Expected: all tests pass. (Smoke tests are not in this default selection because of the `-m smoke` marker.)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Phase 1 complete: ship k8sense ask CLI with eval harness"
```

- [ ] **Step 4: Tag the phase**

```bash
git tag -a phase-1 -m "Phase 1: one-shot CLI investigator with eval harness"
git log --oneline
```

Expected: `phase-1` tag points at the latest commit.

---

## Phase 1 acceptance checklist

After all tasks are complete, verify:

- [ ] `k8sense doctor` exits 0 on a healthy environment.
- [ ] `k8sense ask "list every namespace in the cluster"` produces a coherent answer containing real namespace names from the live cluster.
- [ ] `pytest` passes without `K8SENSE_ALLOW_API`.
- [ ] `K8SENSE_ALLOW_API=1 pytest -m smoke` passes against the real cluster.
- [ ] `python -m evals.runner` writes a report showing ≥8/10 fingerprints passing. (If <8 pass, tighten/loosen fingerprints — but never change the scorer to mask failures.)
- [ ] The git log reads as a clean TDD progression: each test/implementation pair is its own commit.
- [ ] No mocking of `kubectl` or the SDK anywhere in the test suite.

If all boxes are ticked, Phase 1 is shippable and we move to writing the Phase 2 plan (subagents).
