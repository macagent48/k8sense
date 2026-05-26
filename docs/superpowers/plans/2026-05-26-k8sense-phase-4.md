# k8sense Phase 4 Implementation Plan (Hooks, Permission Modes, Incident Journal)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make k8sense able to safely mutate the cluster. Add a PreToolUse hook gating every kubectl call, three permission modes (`readonly` default / `propose` / `auto-safe`), a four-action allowlist with a health precondition on `delete pod`, and an append-only incident journal at `~/.k8sense/journal/incidents.jsonl` that injects similar prior incidents into the prompt for new investigations.

**Architecture:** Three new layered concerns — `permissions.py` resolves the mode (CLI flag > env > config > default), `hooks/` enforces gating via a pure `decide()` truth table wrapped by an async SDK hook callback, and `memory/` extracts signatures and manages the JSONL journal with tiered similarity lookup. `agent.build_options()` wires the hook into `ClaudeAgentOptions.hooks`. `run_ask()` loads prior-incident hints before the agent runs and appends the real signature after it finishes.

**Tech Stack:**

- Phase 3 stack (`claude-agent-sdk`, `mcp`, `rich`, `httpx`, `pytest`, `python-dotenv`, `pydantic`)
- No new dependencies — `tomllib` is stdlib; hooks are part of `claude-agent-sdk`

**Spec:** `docs/superpowers/specs/2026-05-26-k8sense-phase-4-design.md`
**Predecessor:** Phase 3 at git tag `phase-3` (commit `acded36`); current `HEAD` at `e15dbe5` (spec commit)

**Testing discipline:** Inherits Phase 1-3's "strict TDD for deterministic code, eval harness for LLM behaviour." No mocking of kubectl, Prometheus, the SDK, or the MCP server. The "kubectl missing" tests use `monkeypatch.setenv("PATH", str(tmp_path))`.

**SDK reality verified at plan time:**

- `ClaudeAgentOptions.hooks: dict[Literal["PreToolUse" | ...], list[HookMatcher]]`
- `HookMatcher(matcher=str|None, hooks=list[HookCallback], timeout=float|None)`
- `PreToolUseHookInput` is a TypedDict with `tool_name`, `tool_input`, plus standard envelope fields
- Hook returns `SyncHookJSONOutput` with optional `hookSpecificOutput` of `PreToolUseHookSpecificOutput`
- `PreToolUseHookSpecificOutput` has `hookEventName: "PreToolUse"` + optional `permissionDecision: Literal["allow", "deny", "ask", "defer"]` + optional `permissionDecisionReason: str`

---

## File Structure

```
new-project/
├── src/k8sense/
│   ├── cli.py                       # MODIFY: +flags --propose --auto-fix, doctor finding
│   ├── agent.py                     # MODIFY: hook wiring, journal load+append, capture tool_results
│   ├── render.py                    # MODIFY: +proposed_action() method
│   ├── permissions.py               # NEW
│   ├── hooks/                       # NEW package
│   │   ├── __init__.py
│   │   ├── safe_actions.py
│   │   └── pre_tool_use.py
│   └── memory/                      # NEW package
│       ├── __init__.py
│       ├── signature.py
│       └── journal.py
├── tests/unit/
│   ├── test_permissions.py          # NEW
│   ├── test_safe_actions.py         # NEW
│   ├── test_pre_tool_use_hook.py    # NEW
│   ├── test_signature.py            # NEW
│   ├── test_journal.py              # NEW
│   ├── test_render.py               # MODIFY: +proposed_action tests
│   ├── test_cli.py                  # MODIFY: +flag parsing tests
│   └── test_agent_helpers.py        # MODIFY: tool_results capture
├── tests/smoke/
│   └── test_propose_mode.py         # NEW
├── evals/
│   ├── dataset.jsonl                # MODIFY: +3 mutation-path entries (15 → 18)
│   └── runner.py                    # MODIFY: per-entry permission_mode + pass-through
├── pyproject.toml                   # MODIFY: bump to 0.4.0
└── README.md                        # MODIFY: Phase 4 section
```

**Responsibilities:**

- `permissions.py` — `PermissionMode` enum + `resolve()` with priority chain.
- `hooks/safe_actions.py` — pure: `parse_kubectl`, `is_allowlisted`, `decide`. No I/O.
- `hooks/pre_tool_use.py` — thin async wrapper that fetches pod status when needed and emits SDK-shaped output.
- `memory/signature.py` — pure: `Signature` dataclass, `extract()` from completed investigation, `extract_text_hints()` from question alone.
- `memory/journal.py` — JSONL append, tiered similarity lookup, prompt-block formatter.
- `agent.py` — wires hook + journal + tool_results capture into `build_options()` and `run_ask()`.
- `cli.py` — `--propose` / `--auto-fix` flags; passes mode through to `run_ask`.
- `render.py` — `proposed_action(command, hint)` method.

---

## Task 1: Bump version to 0.4.0

**Files:**

- Modify: `pyproject.toml`

- [ ] **Step 1: Bump the project version**

Find in `pyproject.toml`:

```toml
version = "0.3.0"
```

Change to:

```toml
version = "0.4.0"
```

- [ ] **Step 2: Refresh editable install**

Run:

```bash
uv pip install -e ".[dev]"
```

(If `uv pip` isn't available, try `.venv/bin/python -m pip install -e ".[dev]"`. The point is to refresh the editable metadata; if neither works, that's fine — proceed.)

- [ ] **Step 3: Confirm prior tests still pass**

Run:

```bash
.venv/bin/pytest -v
```

Expected: 207 passed, 3 skipped.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "Bump to 0.4.0 for Phase 4"
```

---

## Task 2: `permissions.py` (TDD)

**Files:**

- Create: `src/k8sense/permissions.py`
- Create: `tests/unit/test_permissions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_permissions.py`:

```python
"""PermissionMode + resolve() priority chain: flag > env > config > default."""
import pytest

from k8sense.permissions import (
    DEFAULT_MODE,
    ENV_VAR,
    CONFIG_PATH,
    PermissionMode,
    resolve,
)


def test_permission_mode_values():
    assert PermissionMode.READONLY.value == "readonly"
    assert PermissionMode.PROPOSE.value == "propose"
    assert PermissionMode.AUTO_SAFE.value == "auto-safe"


def test_default_mode_is_readonly():
    assert DEFAULT_MODE == PermissionMode.READONLY


def test_resolve_returns_default_when_no_overrides(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", tmp_path / "absent.toml")
    assert resolve() == PermissionMode.READONLY


def test_resolve_uses_flag_when_provided(monkeypatch):
    # Flag wins even when env is set
    monkeypatch.setenv(ENV_VAR, "auto-safe")
    assert resolve(flag_value="propose") == PermissionMode.PROPOSE


def test_resolve_raises_on_invalid_flag():
    with pytest.raises(ValueError, match="invalid permission mode"):
        resolve(flag_value="dangerous")


def test_resolve_uses_env_when_no_flag(monkeypatch, tmp_path):
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", tmp_path / "absent.toml")
    monkeypatch.setenv(ENV_VAR, "auto-safe")
    assert resolve() == PermissionMode.AUTO_SAFE


def test_resolve_falls_through_invalid_env_to_default(monkeypatch, tmp_path):
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", tmp_path / "absent.toml")
    monkeypatch.setenv(ENV_VAR, "nonsense")
    assert resolve() == PermissionMode.READONLY


def test_resolve_uses_config_when_no_flag_or_env(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    cfg = tmp_path / "config.toml"
    cfg.write_text('permission_mode = "propose"\n', encoding="utf-8")
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", cfg)
    assert resolve() == PermissionMode.PROPOSE


def test_resolve_falls_through_invalid_config_to_default(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    cfg = tmp_path / "config.toml"
    cfg.write_text('permission_mode = "nonsense"\n', encoding="utf-8")
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", cfg)
    assert resolve() == PermissionMode.READONLY


def test_resolve_falls_through_malformed_toml_to_default(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    cfg = tmp_path / "config.toml"
    cfg.write_text("permission_mode = unquoted-value", encoding="utf-8")
    monkeypatch.setattr("k8sense.permissions.CONFIG_PATH", cfg)
    assert resolve() == PermissionMode.READONLY
```

- [ ] **Step 2: Run — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_permissions.py -v
```

Expected: `ModuleNotFoundError: No module named 'k8sense.permissions'`.

- [ ] **Step 3: Implement**

Create `src/k8sense/permissions.py`:

```python
"""Permission mode resolution: CLI flag > env var > config file > default."""
from __future__ import annotations

import os
import tomllib
from enum import Enum
from pathlib import Path


class PermissionMode(str, Enum):
    READONLY = "readonly"
    PROPOSE = "propose"
    AUTO_SAFE = "auto-safe"


DEFAULT_MODE = PermissionMode.READONLY
CONFIG_PATH = Path.home() / ".k8sense" / "config.toml"
ENV_VAR = "K8SENSE_PERMISSION_MODE"


def _parse(value: str) -> PermissionMode | None:
    """Parse a string into a PermissionMode; None on unknown value."""
    try:
        return PermissionMode(value)
    except ValueError:
        return None


def _from_config_file() -> PermissionMode | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return None
    raw = data.get("permission_mode")
    return _parse(raw) if isinstance(raw, str) else None


def resolve(flag_value: str | None = None) -> PermissionMode:
    """Resolve effective mode: flag > env > config > default.

    Raises ValueError if `flag_value` is a non-None unknown string (CLI error).
    Bad env / config values silently fall through to lower-priority sources.
    """
    if flag_value is not None:
        parsed = _parse(flag_value)
        if parsed is None:
            raise ValueError(f"invalid permission mode flag value: {flag_value!r}")
        return parsed

    env = os.environ.get(ENV_VAR)
    if env:
        parsed = _parse(env)
        if parsed is not None:
            return parsed

    cfg = _from_config_file()
    if cfg is not None:
        return cfg

    return DEFAULT_MODE
```

- [ ] **Step 4: Run — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_permissions.py -v
```

Expected: 10 tests pass.

- [ ] **Step 5: Run full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: 217 passed, 3 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/permissions.py tests/unit/test_permissions.py
git commit -m "Add PermissionMode + resolve() priority chain"
```

---

## Task 3: `hooks/safe_actions.py` — parse_kubectl (TDD)

**Files:**

- Create: `src/k8sense/hooks/__init__.py`
- Create: `src/k8sense/hooks/safe_actions.py`
- Create: `tests/unit/test_safe_actions.py`

- [ ] **Step 1: Write the failing tests for parse_kubectl**

Create `tests/unit/test_safe_actions.py`:

```python
"""Tests for hooks.safe_actions: parse_kubectl, is_allowlisted, decide."""
import pytest

from k8sense.hooks.safe_actions import KubectlInvocation, parse_kubectl


def test_parse_get_pods():
    inv = parse_kubectl(["get", "pods", "-n", "argocd"])
    assert inv.verb == "get"
    assert inv.resource_kind == "pods"
    assert inv.namespace == "argocd"
    assert inv.name is None


def test_parse_describe_pod_with_name():
    inv = parse_kubectl(["describe", "pod", "argocd-server-7d", "-n", "argocd"])
    assert inv.verb == "describe"
    assert inv.resource_kind == "pod"
    assert inv.name == "argocd-server-7d"
    assert inv.namespace == "argocd"


def test_parse_delete_pod():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    assert inv.verb == "delete"
    assert inv.resource_kind == "pod"
    assert inv.name == "x"
    assert inv.namespace == "argocd"


def test_parse_rollout_restart_deployment_slash_name():
    inv = parse_kubectl(["rollout", "restart", "deployment/argocd-server", "-n", "argocd"])
    assert inv.verb == "rollout"
    assert inv.resource_kind == "deployment"
    assert inv.name == "argocd-server"
    assert inv.namespace == "argocd"
    # We can also peek at the rollout subverb via flags or args
    assert "restart" in inv.args


def test_parse_cordon_node():
    inv = parse_kubectl(["cordon", "worker1"])
    assert inv.verb == "cordon"
    assert inv.resource_kind == "node"
    assert inv.name == "worker1"
    assert inv.namespace is None


def test_parse_delete_with_field_selector():
    inv = parse_kubectl(["delete", "pod", "--field-selector=status.phase=Succeeded", "-n", "argocd"])
    assert inv.verb == "delete"
    assert inv.resource_kind == "pod"
    # name is absent because no positional after pod
    assert inv.name is None
    assert inv.namespace == "argocd"
    assert inv.flags.get("field-selector") == "status.phase=Succeeded"


def test_parse_namespace_via_dash_dash_namespace():
    inv = parse_kubectl(["get", "pods", "--namespace=longhorn-system"])
    assert inv.namespace == "longhorn-system"


def test_parse_namespace_via_dash_dash_namespace_space():
    inv = parse_kubectl(["get", "pods", "--namespace", "longhorn-system"])
    assert inv.namespace == "longhorn-system"


def test_parse_empty_args_returns_unknown():
    inv = parse_kubectl([])
    assert inv.verb == "<unknown>"


def test_parse_unknown_verb_keeps_args():
    inv = parse_kubectl(["frobnicate", "pod", "x"])
    assert inv.verb == "frobnicate"
    # Best-effort: resource_kind may still be parsed
```

- [ ] **Step 2: Run — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_safe_actions.py -v
```

Expected: `ModuleNotFoundError: No module named 'k8sense.hooks'`.

- [ ] **Step 3: Create the package init**

Create `src/k8sense/hooks/__init__.py`:

```python
"""PreToolUse hook for k8sense Phase 4 — gates kubectl mutations."""
```

- [ ] **Step 4: Implement parse_kubectl**

Create `src/k8sense/hooks/safe_actions.py`:

```python
"""Pure logic for the PreToolUse hook.

parse_kubectl: structured form of kubectl argv
is_allowlisted: True iff invocation matches one of the four safe actions
decide: truth table allow / deny / propose given (invocation, mode, pod_status)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from k8sense.permissions import PermissionMode

_KIND_ALIASES = {
    "pod": "pod",
    "pods": "pods",
    "po": "pod",
    "deployment": "deployment",
    "deployments": "deployments",
    "deploy": "deployment",
    "node": "node",
    "nodes": "nodes",
    "no": "node",
}


@dataclass(frozen=True)
class KubectlInvocation:
    verb: str
    args: list[str]
    resource_kind: str | None
    name: str | None
    namespace: str | None
    flags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    behaviour: Literal["allow", "deny", "propose"]
    message: str


def _parse_flags_and_positionals(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Split `args` into positionals and a flag dict.

    Handles --flag=value, --flag value, and bare --flag (mapped to "").
    The -n shorthand and --namespace are both treated as flags.
    """
    positionals: list[str] = []
    flags: dict[str, str] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("--"):
            if "=" in token:
                key, _, value = token[2:].partition("=")
                flags[key] = value
            else:
                key = token[2:]
                if i + 1 < len(args) and not args[i + 1].startswith("-"):
                    flags[key] = args[i + 1]
                    i += 1
                else:
                    flags[key] = ""
        elif token == "-n" and i + 1 < len(args):
            flags["namespace"] = args[i + 1]
            i += 1
        else:
            positionals.append(token)
        i += 1
    return positionals, flags


def parse_kubectl(args: list[str]) -> KubectlInvocation:
    """Best-effort parse of kubectl argv. Unknown shapes return verb='<unknown>'."""
    if not args:
        return KubectlInvocation(
            verb="<unknown>", args=[], resource_kind=None, name=None, namespace=None
        )

    positionals, flags = _parse_flags_and_positionals(args)
    verb = positionals[0] if positionals else "<unknown>"
    namespace = flags.get("namespace")

    resource_kind: str | None = None
    name: str | None = None

    # Shape A: <verb> <kind> [name] ...
    if len(positionals) >= 2:
        kind_token = positionals[1]
        # Shape B: <verb> kind/name (e.g. rollout restart deployment/argocd-server)
        if "/" in kind_token:
            kind_part, _, name_part = kind_token.partition("/")
            resource_kind = _KIND_ALIASES.get(kind_part, kind_part)
            name = name_part or None
        else:
            resource_kind = _KIND_ALIASES.get(kind_token, kind_token)
            if len(positionals) >= 3:
                name = positionals[2]

    # Shape C: <verb> <node-name>  (cordon / drain / etc — name follows verb directly)
    if verb in {"cordon", "drain", "uncordon"} and len(positionals) >= 2 and resource_kind in {None, "node", "nodes"}:
        resource_kind = "node"
        name = positionals[1]

    # Shape D: rollout restart deployment/<name> -n <ns>
    if verb == "rollout" and len(positionals) >= 3:
        # positionals are: rollout, restart, deployment/name
        subverb_token = positionals[1]  # "restart"
        target_token = positionals[2]   # "deployment/argocd-server"
        if "/" in target_token:
            kind_part, _, name_part = target_token.partition("/")
            resource_kind = _KIND_ALIASES.get(kind_part, kind_part)
            name = name_part or None

    return KubectlInvocation(
        verb=verb,
        args=args,
        resource_kind=resource_kind,
        name=name,
        namespace=namespace,
        flags=flags,
    )
```

- [ ] **Step 5: Run — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_safe_actions.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/hooks/__init__.py src/k8sense/hooks/safe_actions.py tests/unit/test_safe_actions.py
git commit -m "Add parse_kubectl for hook input parsing"
```

---

## Task 4: `hooks/safe_actions.py` — is_allowlisted + decide (TDD)

**Files:**

- Modify: `src/k8sense/hooks/safe_actions.py`
- Modify: `tests/unit/test_safe_actions.py`

- [ ] **Step 1: Append failing tests for is_allowlisted**

Append to `tests/unit/test_safe_actions.py`:

```python


from k8sense.hooks.safe_actions import is_allowlisted, is_read_only  # noqa: E402


@pytest.mark.parametrize("verb", ["get", "describe", "logs", "top", "events", "version"])
def test_read_only_verbs(verb):
    assert is_read_only(verb) is True


@pytest.mark.parametrize("verb", ["delete", "rollout", "cordon", "apply", "scale"])
def test_non_read_only_verbs(verb):
    assert is_read_only(verb) is False


def test_allowlist_delete_pod_with_unhealthy_status():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    assert is_allowlisted(inv, pod_status="CrashLoopBackOff") is True
    assert is_allowlisted(inv, pod_status="ImagePullBackOff") is True
    assert is_allowlisted(inv, pod_status="Error") is True
    assert is_allowlisted(inv, pod_status="Unknown") is True
    assert is_allowlisted(inv, pod_status="Pending") is True


def test_allowlist_delete_pod_rejects_healthy_status():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    assert is_allowlisted(inv, pod_status="Running") is False
    assert is_allowlisted(inv, pod_status="Succeeded") is False


def test_allowlist_delete_pod_rejects_unknown_status_fail_closed():
    """When status can't be determined, fail closed."""
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    assert is_allowlisted(inv, pod_status=None) is False


def test_allowlist_rollout_restart_deployment():
    inv = parse_kubectl(["rollout", "restart", "deployment/argocd-server", "-n", "argocd"])
    assert is_allowlisted(inv, pod_status=None) is True


def test_allowlist_cordon_node():
    inv = parse_kubectl(["cordon", "worker1"])
    assert is_allowlisted(inv, pod_status=None) is True


def test_allowlist_delete_pod_field_selector_succeeded():
    """Cleanup action: delete pods with --field-selector=status.phase=Succeeded."""
    inv = parse_kubectl([
        "delete", "pod", "--field-selector=status.phase=Succeeded", "-n", "argocd",
    ])
    # Status precondition doesn't apply when using field-selector cleanup
    assert is_allowlisted(inv, pod_status=None) is True


def test_allowlist_rejects_other_mutations():
    for argv in [
        ["apply", "-f", "manifest.yaml"],
        ["scale", "deployment/x", "--replicas=0", "-n", "argocd"],
        ["delete", "deployment", "x", "-n", "argocd"],
        ["edit", "pod", "x"],
        ["drain", "worker1"],
        ["delete", "job", "x", "-n", "argocd"],
    ]:
        inv = parse_kubectl(argv)
        assert is_allowlisted(inv, pod_status="CrashLoopBackOff") is False, f"should reject: {argv}"
```

- [ ] **Step 2: Run — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_safe_actions.py -v
```

Expected: new tests fail with `ImportError: cannot import name 'is_allowlisted'`.

- [ ] **Step 3: Append is_allowlisted + is_read_only to safe_actions.py**

Append to `src/k8sense/hooks/safe_actions.py`:

```python


_UNHEALTHY_STATUSES = frozenset({
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "Error",
    "Unknown",
    "Pending",
})


def is_read_only(verb: str) -> bool:
    return verb in {"get", "describe", "logs", "top", "events", "version"}


def is_allowlisted(invocation: KubectlInvocation, pod_status: str | None) -> bool:
    """True iff this invocation matches one of the four safe actions.

    Action 1: delete pod <name> -n <ns>          [requires unhealthy pod_status]
    Action 2: rollout restart deployment/<name>  [no precondition]
    Action 3: cordon <node>                      [no precondition]
    Action 4: delete pod --field-selector=status.phase=Succeeded  [no precondition]
    """
    # Action 4 (must check before Action 1 to bypass status precondition)
    if (
        invocation.verb == "delete"
        and invocation.resource_kind in {"pod", "pods"}
        and invocation.flags.get("field-selector") == "status.phase=Succeeded"
    ):
        return True

    # Action 1
    if (
        invocation.verb == "delete"
        and invocation.resource_kind in {"pod", "pods"}
        and invocation.name
        and not invocation.flags.get("field-selector")  # not the cleanup variant
    ):
        return pod_status in _UNHEALTHY_STATUSES

    # Action 2
    if (
        invocation.verb == "rollout"
        and invocation.resource_kind in {"deployment", "deployments"}
        and "restart" in invocation.args
        and invocation.name
    ):
        return True

    # Action 3
    if invocation.verb == "cordon" and invocation.resource_kind == "node" and invocation.name:
        return True

    return False
```

- [ ] **Step 4: Run — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_safe_actions.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Append failing tests for decide()**

Append to `tests/unit/test_safe_actions.py`:

```python


from k8sense.hooks.safe_actions import Decision, decide  # noqa: E402
from k8sense.permissions import PermissionMode  # noqa: E402


def test_decide_read_only_always_allow():
    inv = parse_kubectl(["get", "pods"])
    for mode in PermissionMode:
        d = decide(inv, mode, pod_status=None)
        assert d.behaviour == "allow", f"{mode} should allow read-only"


def test_decide_allowlisted_mutation_under_readonly_is_deny():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    d = decide(inv, PermissionMode.READONLY, pod_status="CrashLoopBackOff")
    assert d.behaviour == "deny"
    assert d.message  # has a message


def test_decide_allowlisted_mutation_under_propose_is_propose():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    d = decide(inv, PermissionMode.PROPOSE, pod_status="CrashLoopBackOff")
    assert d.behaviour == "propose"


def test_decide_allowlisted_mutation_under_autosafe_is_allow():
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    d = decide(inv, PermissionMode.AUTO_SAFE, pod_status="CrashLoopBackOff")
    assert d.behaviour == "allow"


def test_decide_non_allowlisted_mutation_always_deny():
    inv = parse_kubectl(["apply", "-f", "manifest.yaml"])
    for mode in PermissionMode:
        d = decide(inv, mode, pod_status=None)
        assert d.behaviour == "deny", f"{mode} should deny non-allowlisted mutation"


def test_decide_delete_pod_with_unknown_status_is_deny_in_autosafe():
    """Fail closed: status unknown → deny even in auto-safe."""
    inv = parse_kubectl(["delete", "pod", "x", "-n", "argocd"])
    d = decide(inv, PermissionMode.AUTO_SAFE, pod_status=None)
    assert d.behaviour == "deny"


def test_decide_cleanup_pod_under_autosafe_is_allow_without_status():
    inv = parse_kubectl([
        "delete", "pod", "--field-selector=status.phase=Succeeded", "-n", "argocd",
    ])
    d = decide(inv, PermissionMode.AUTO_SAFE, pod_status=None)
    assert d.behaviour == "allow"
```

- [ ] **Step 6: Run — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_safe_actions.py -v
```

Expected: 7 new tests fail with `ImportError: cannot import name 'decide'`.

- [ ] **Step 7: Append decide() to safe_actions.py**

Append to `src/k8sense/hooks/safe_actions.py`:

```python


def decide(
    invocation: KubectlInvocation,
    mode: PermissionMode,
    pod_status: str | None = None,
) -> Decision:
    """Pure dispatch table.

                       readonly      propose       auto-safe
    read-only verb     allow         allow         allow
    allowlisted mut    deny          propose       allow
    other mutation     deny          deny          deny
    """
    if is_read_only(invocation.verb):
        return Decision(behaviour="allow", message="read-only verb")

    allowlisted = is_allowlisted(invocation, pod_status)
    if allowlisted:
        if mode == PermissionMode.READONLY:
            return Decision(
                behaviour="deny",
                message="mutations are blocked in readonly mode; re-run with --auto-fix or --propose",
            )
        if mode == PermissionMode.PROPOSE:
            return Decision(
                behaviour="propose",
                message="allowlisted mutation surfaced for review in propose mode",
            )
        return Decision(behaviour="allow", message="allowlisted in auto-safe mode")

    # Non-allowlisted mutation
    return Decision(
        behaviour="deny",
        message=(
            f"verb {invocation.verb!r} not in safe-action allowlist; "
            "only delete pod / rollout restart deployment / cordon node / "
            "delete pod (cleanup) are permitted in auto-safe mode"
        ),
    )
```

- [ ] **Step 8: Run — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_safe_actions.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Run the full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~245 passed, 3 skipped (217 prior + parse tests + allowlist + decide).

- [ ] **Step 10: Commit**

```bash
git add src/k8sense/hooks/safe_actions.py tests/unit/test_safe_actions.py
git commit -m "Add is_allowlisted + decide truth table"
```

---

## Task 5: `hooks/pre_tool_use.py` — async SDK callback (TDD)

**Files:**

- Create: `src/k8sense/hooks/pre_tool_use.py`
- Create: `tests/unit/test_pre_tool_use_hook.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_pre_tool_use_hook.py`:

```python
"""The async PreToolUse hook callback returned by build_pre_tool_use_hook."""
import pytest

from k8sense.hooks.pre_tool_use import build_pre_tool_use_hook
from k8sense.permissions import PermissionMode


def _input(args):
    """Build a minimal PreToolUseHookInput-shaped dict."""
    return {
        "session_id": "s",
        "transcript_path": "/tmp/t",
        "cwd": "/tmp",
        "agent_id": "a",
        "agent_type": "main",
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__k8sense__kubectl",
        "tool_input": {"args": args},
        "tool_use_id": "u",
    }


@pytest.mark.asyncio
async def test_hook_passes_through_non_kubectl_tools():
    hook = build_pre_tool_use_hook(PermissionMode.READONLY)
    input_ = _input(["delete", "pod", "x", "-n", "argocd"]).copy()
    input_["tool_name"] = "mcp__k8sense__prometheus_query"
    result = await hook(input_, "u", None)
    assert result == {}  # empty = let it through


@pytest.mark.asyncio
async def test_hook_allows_read_only_kubectl():
    hook = build_pre_tool_use_hook(PermissionMode.READONLY)
    result = await hook(_input(["get", "pods", "-n", "argocd"]), "u", None)
    assert result == {}


@pytest.mark.asyncio
async def test_hook_denies_mutation_in_readonly(monkeypatch, tmp_path):
    # Hide kubectl so the hook's _fetch_pod_status returns None (cluster unreachable simulation)
    monkeypatch.setenv("PATH", str(tmp_path))
    hook = build_pre_tool_use_hook(PermissionMode.READONLY)
    result = await hook(_input(["delete", "pod", "x", "-n", "argocd"]), "u", None)
    assert "hookSpecificOutput" in result
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_hook_denies_non_allowlisted_in_autosafe(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    hook = build_pre_tool_use_hook(PermissionMode.AUTO_SAFE)
    # `apply` is never allowlisted
    result = await hook(_input(["apply", "-f", "manifest.yaml"]), "u", None)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "not in safe-action allowlist" in result["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.asyncio
async def test_hook_propose_calls_on_propose_callback(monkeypatch, tmp_path):
    """In propose mode, an allowlisted mutation is denied AND the on_propose sink fires."""
    # We can't easily fake an unhealthy pod status here, so use the cleanup variant
    # which doesn't need a status precondition.
    monkeypatch.setenv("PATH", str(tmp_path))
    captured: list[tuple[str, str]] = []
    hook = build_pre_tool_use_hook(
        PermissionMode.PROPOSE,
        on_propose=lambda cmd, msg: captured.append((cmd, msg)),
    )
    result = await hook(_input([
        "delete", "pod", "--field-selector=status.phase=Succeeded", "-n", "argocd",
    ]), "u", None)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Proposed" in result["hookSpecificOutput"]["permissionDecisionReason"]
    assert len(captured) == 1
    cmd, _msg = captured[0]
    assert cmd.startswith("kubectl delete pod ")
    assert "Succeeded" in cmd


@pytest.mark.asyncio
async def test_hook_allows_cleanup_in_autosafe(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    hook = build_pre_tool_use_hook(PermissionMode.AUTO_SAFE)
    result = await hook(_input([
        "delete", "pod", "--field-selector=status.phase=Succeeded", "-n", "argocd",
    ]), "u", None)
    assert result == {}  # allow
```

- [ ] **Step 2: Run — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_pre_tool_use_hook.py -v
```

Expected: `ModuleNotFoundError: No module named 'k8sense.hooks.pre_tool_use'`.

- [ ] **Step 3: Implement**

Create `src/k8sense/hooks/pre_tool_use.py`:

```python
"""SDK PreToolUse hook callback. Fetches pod status when needed; defers to safe_actions.decide."""
from __future__ import annotations

import shlex
from typing import Any, Callable

from k8sense.hooks.safe_actions import decide, parse_kubectl
from k8sense.permissions import PermissionMode
from k8sense.tools.kubectl import run_kubectl

_KUBECTL_TOOL_NAME = "mcp__k8sense__kubectl"


async def _fetch_pod_status(name: str, namespace: str) -> str | None:
    """Return the pod's status.phase via kubectl, or None if it can't be determined.

    A None return triggers the fail-closed branch in safe_actions.decide().
    """
    result = await run_kubectl([
        "get", "pod", name, "-n", namespace,
        "-o", "jsonpath={.status.phase}",
    ])
    if result["exit_code"] != 0:
        return None
    phase = result["stdout"].strip()
    return phase or None


def build_pre_tool_use_hook(
    mode: PermissionMode,
    on_propose: Callable[[str, str], None] | None = None,
):
    """Return an async hook callback closed over `mode` and the propose sink.

    on_propose: invoked with (command_string, decision.message) when a mutation
    is intercepted in propose mode. CLI plugs the renderer in here; Phase 5
    sentinel will plug Telegram in here.
    """

    async def hook(
        input_: dict[str, Any],
        tool_use_id: str | None,
        ctx: Any,
    ) -> dict[str, Any]:
        if input_.get("tool_name") != _KUBECTL_TOOL_NAME:
            return {}

        args = input_.get("tool_input", {}).get("args", [])
        invocation = parse_kubectl(args)

        pod_status: str | None = None
        if invocation.verb == "delete" and invocation.resource_kind in {"pod", "pods"} and invocation.name:
            pod_status = await _fetch_pod_status(invocation.name, invocation.namespace or "default")

        decision = decide(invocation, mode, pod_status=pod_status)

        if decision.behaviour == "allow":
            return {}

        if decision.behaviour == "propose":
            command = "kubectl " + " ".join(shlex.quote(a) for a in args)
            if on_propose is not None:
                on_propose(command, decision.message)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Proposed (not executed in propose mode): {command}"
                    ),
                }
            }

        # deny
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.message,
            }
        }

    return hook
```

- [ ] **Step 4: Run — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_pre_tool_use_hook.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Run full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~251 passed, 3 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/hooks/pre_tool_use.py tests/unit/test_pre_tool_use_hook.py
git commit -m "Add async PreToolUse hook callback (allow/deny/propose)"
```

---

## Task 6: `memory/signature.py` — extract + extract_text_hints (TDD)

**Files:**

- Create: `src/k8sense/memory/__init__.py`
- Create: `src/k8sense/memory/signature.py`
- Create: `tests/unit/test_signature.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_signature.py`:

```python
"""memory.signature: extract structured (kind, namespace, name, reason) from an investigation."""
import pytest

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
        tool_calls=[_tool_call(["describe", "pod", "argocd-server-7d", "-n", "argocd"])],
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
```

- [ ] **Step 2: Run — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_signature.py -v
```

Expected: `ModuleNotFoundError: No module named 'k8sense.memory'`.

- [ ] **Step 3: Create package init**

Create `src/k8sense/memory/__init__.py`:

```python
"""k8sense incident journal (memory) — Phase 4."""
```

- [ ] **Step 4: Implement**

Create `src/k8sense/memory/signature.py`:

```python
"""Pure signature extraction from a completed investigation, or hints from a question."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

# Most specific first; first match wins.
_REASON_PATTERNS = (
    "OOMKilled",
    "ImagePullBackOff",
    "ErrImagePull",
    "CrashLoopBackOff",
    "ContainerCreating",
    "Evicted",
    "NodeNotReady",
    "FailedScheduling",
    "Unhealthy",
    "BackOff",
    "Pending",
)

_KIND_NORMALISE = {
    "pod": "Pod",
    "pods": "Pod",
    "po": "Pod",
    "deployment": "Deployment",
    "deployments": "Deployment",
    "deploy": "Deployment",
    "node": "Node",
    "nodes": "Node",
    "no": "Node",
}


@dataclass(frozen=True)
class Signature:
    kind: str | None
    namespace: str | None
    name: str | None
    reason: str | None

    def is_empty(self) -> bool:
        return not any((self.kind, self.namespace, self.name, self.reason))


def _scan_for_reason(haystacks: list[str]) -> str | None:
    for pattern in _REASON_PATTERNS:
        for h in haystacks:
            if pattern in h:
                return pattern
    return None


def _extract_resource_target(
    tool_calls: list[dict],
) -> tuple[str | None, str | None, str | None]:
    """Find the (kind, namespace, name) most discussed in describe/logs invocations."""
    counter: Counter[tuple[str, str | None, str | None]] = Counter()

    for call in tool_calls:
        args = (call.get("input") or {}).get("args", [])
        if not args:
            continue
        verb = args[0]
        if verb not in {"describe", "logs"}:
            continue

        # logs: args[1] is the pod name
        # describe: args[1] is the kind, args[2] is the name
        kind_raw: str | None = None
        name: str | None = None
        namespace: str | None = None

        if verb == "describe" and len(args) >= 3:
            kind_raw = args[1]
            name = args[2]
        elif verb == "logs" and len(args) >= 2:
            kind_raw = "pod"
            name = args[1]

        # find -n or --namespace
        i = 0
        while i < len(args):
            if args[i] == "-n" and i + 1 < len(args):
                namespace = args[i + 1]
                break
            if args[i].startswith("--namespace"):
                if "=" in args[i]:
                    namespace = args[i].split("=", 1)[1]
                elif i + 1 < len(args):
                    namespace = args[i + 1]
                break
            i += 1

        if kind_raw and name:
            kind = _KIND_NORMALISE.get(kind_raw, kind_raw)
            counter[(kind, namespace, name)] += 1

    if not counter:
        return (None, None, None)
    (kind, namespace, name), _count = counter.most_common(1)[0]
    return (kind, namespace, name)


def extract(
    tool_calls: list[dict],
    tool_results: list[dict],
    final_text: str,
) -> Signature:
    """Best-effort signature from a completed investigation."""
    kind, namespace, name = _extract_resource_target(tool_calls)
    haystacks = [final_text] + [r.get("text", "") for r in tool_results if isinstance(r, dict)]
    reason = _scan_for_reason(haystacks)
    return Signature(kind=kind, namespace=namespace, name=name, reason=reason)


def extract_text_hints(question: str) -> Signature:
    """Best-effort guess at signature from the question text alone.

    Returns whatever can be derived. In Phase 4 we only scan for reason keywords;
    namespace/name inference from raw question text would require topology lookup
    or NER and is deferred to Phase 4.1+.
    """
    reason = _scan_for_reason([question])
    return Signature(kind=None, namespace=None, name=None, reason=reason)
```

- [ ] **Step 5: Run — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_signature.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/memory/__init__.py src/k8sense/memory/signature.py tests/unit/test_signature.py
git commit -m "Add Signature dataclass + extract/extract_text_hints"
```

---

## Task 7: `memory/journal.py` — JSONL store + tiered lookup (TDD)

**Files:**

- Create: `src/k8sense/memory/journal.py`
- Create: `tests/unit/test_journal.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_journal.py`:

```python
"""memory.journal: append, load, tiered similarity lookup, prompt formatting."""
from pathlib import Path

import pytest

from k8sense.memory.journal import (
    append_entry,
    find_similar,
    format_for_prompt,
    load_all,
)
from k8sense.memory.signature import Signature


@pytest.fixture
def journal_path(tmp_path, monkeypatch):
    path = tmp_path / "incidents.jsonl"
    monkeypatch.setattr("k8sense.memory.journal.JOURNAL_DIR", tmp_path)
    monkeypatch.setattr("k8sense.memory.journal.JOURNAL_PATH", path)
    return path


def test_load_all_returns_empty_when_file_absent(journal_path):
    assert load_all() == []


def test_append_entry_creates_directory_and_file(journal_path):
    sig = Signature(kind="Pod", namespace="argocd", name="x", reason="OOMKilled")
    append_entry(
        question="why?",
        final_text="OOMKilled",
        tool_calls=[],
        tool_results=[],
        signature=sig,
        actions_taken=[],
        mode="readonly",
    )
    assert journal_path.exists()
    entries = load_all()
    assert len(entries) == 1
    assert entries[0]["signature"] == {
        "kind": "Pod", "namespace": "argocd", "name": "x", "reason": "OOMKilled",
    }
    assert entries[0]["mode"] == "readonly"
    assert "timestamp" in entries[0]


def test_load_all_skips_malformed_lines(journal_path, tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(
        '{"signature": {"kind": "Pod"}, "timestamp": "t1"}\n'
        'not json at all\n'
        '{"signature": {"kind": "Node"}, "timestamp": "t2"}\n',
        encoding="utf-8",
    )
    entries = load_all()
    assert len(entries) == 2


def _entry(kind, namespace, name, reason, ts):
    return {
        "timestamp": ts,
        "signature": {"kind": kind, "namespace": namespace, "name": name, "reason": reason},
        "resolution": f"resolved {reason}",
    }


def test_find_similar_returns_empty_for_empty_signature():
    entries = [_entry("Pod", "argocd", "x", "OOMKilled", "t1")]
    sig = Signature(kind=None, namespace=None, name=None, reason=None)
    assert find_similar(sig, entries) == []


def test_find_similar_exact_match():
    entries = [
        _entry("Pod", "argocd", "x", "OOMKilled", "2026-01-01"),
        _entry("Pod", "argocd", "x", "OOMKilled", "2026-02-01"),
        _entry("Pod", "longhorn", "y", "OOMKilled", "2026-03-01"),
    ]
    sig = Signature(kind="Pod", namespace="argocd", name="x", reason="OOMKilled")
    result = find_similar(sig, entries)
    # Both exact-match entries returned, most recent first
    assert len(result) == 2
    assert result[0]["timestamp"] == "2026-02-01"
    assert result[1]["timestamp"] == "2026-01-01"


def test_find_similar_falls_through_to_namespace_tier():
    entries = [
        _entry("Pod", "argocd", "y", "OOMKilled", "2026-01-01"),  # same ns + reason, different name
        _entry("Pod", "longhorn", "z", "OOMKilled", "2026-02-01"),  # different ns
    ]
    sig = Signature(kind="Pod", namespace="argocd", name="x", reason="OOMKilled")
    result = find_similar(sig, entries)
    # tier 1 (exact) = 0 hits; tier 2 (ns+reason) = 1 hit; tier 3 (reason) = 2 hits but we already have 1
    # Result should include the tier-2 entry first, then the tier-3 entry to fill
    assert len(result) == 2
    assert result[0]["signature"]["namespace"] == "argocd"


def test_find_similar_respects_limit():
    entries = [
        _entry("Pod", "argocd", "x", "OOMKilled", f"2026-01-0{i}")
        for i in range(1, 9)
    ]
    sig = Signature(kind="Pod", namespace="argocd", name="x", reason="OOMKilled")
    result = find_similar(sig, entries, limit=3)
    assert len(result) == 3


def test_format_for_prompt_empty_returns_empty_string():
    assert format_for_prompt([]) == ""


def test_format_for_prompt_renders_markdown_block():
    entries = [
        _entry("Pod", "argocd", "x", "OOMKilled", "2026-01-01T00:00:00Z"),
    ]
    text = format_for_prompt(entries)
    assert "Prior incidents" in text
    assert "OOMKilled" in text
    assert "argocd" in text
```

- [ ] **Step 2: Run — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_journal.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/k8sense/memory/journal.py`:

```python
"""Incident journal: append-only JSONL with tiered similarity lookup."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from k8sense.memory.signature import Signature

JOURNAL_DIR = Path.home() / ".k8sense" / "journal"
JOURNAL_PATH = JOURNAL_DIR / "incidents.jsonl"


def append_entry(
    question: str,
    final_text: str,
    tool_calls: list[dict],
    tool_results: list[dict],
    signature: Signature,
    actions_taken: list[str],
    mode: str,
    severity: str = "info",
) -> None:
    """Append one investigation to the journal. Idempotent in directory creation."""
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "summary": final_text[:500],
        "tools_called": tool_calls,
        "tool_results": tool_results,
        "resolution": final_text[-500:] if len(final_text) > 500 else final_text,
        "signature": {
            "kind": signature.kind,
            "namespace": signature.namespace,
            "name": signature.name,
            "reason": signature.reason,
        },
        "actions_taken": actions_taken,
        "mode": mode,
        "severity": severity,
    }
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_all(path: Path | None = None) -> list[dict[str, Any]]:
    """Load every journal entry. Skips malformed lines silently."""
    p = path if path is not None else JOURNAL_PATH
    if not p.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def find_similar(
    signature: Signature,
    entries: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Tiered lookup. Up to `limit` entries, most recent first within each tier.

    Tier 1: exact (kind, namespace, name, reason)
    Tier 2: (kind, namespace, reason) — ignore name
    Tier 3: (kind, reason)             — ignore namespace + name
    """
    if signature.is_empty():
        return []

    matched_indices: set[int] = set()
    out: list[dict[str, Any]] = []

    def _try_tier(predicate) -> None:
        # Walk entries newest-last → iterate reversed
        for i in range(len(entries) - 1, -1, -1):
            if len(out) >= limit:
                return
            if i in matched_indices:
                continue
            sig = entries[i].get("signature", {})
            if predicate(sig):
                matched_indices.add(i)
                out.append(entries[i])

    _try_tier(lambda s: (
        s.get("kind") == signature.kind
        and s.get("namespace") == signature.namespace
        and s.get("name") == signature.name
        and s.get("reason") == signature.reason
    ))
    _try_tier(lambda s: (
        s.get("kind") == signature.kind
        and s.get("namespace") == signature.namespace
        and s.get("reason") == signature.reason
    ))
    _try_tier(lambda s: (
        s.get("kind") == signature.kind
        and s.get("reason") == signature.reason
    ))

    return out


def format_for_prompt(similar_entries: list[dict[str, Any]]) -> str:
    """Render similar incidents as a markdown block for injection into the user message."""
    if not similar_entries:
        return ""
    lines = ["## Prior incidents (most recent first)", ""]
    for i, e in enumerate(similar_entries, 1):
        sig = e.get("signature", {})
        lines.append(
            f"{i}. [{e.get('timestamp', '')[:10]}] "
            f"{sig.get('kind')}/{sig.get('namespace')}/{sig.get('name')} "
            f"— reason: {sig.get('reason')}"
        )
        resolution = e.get("resolution") or "<no summary>"
        lines.append(f"   resolved: {resolution[:200]}")
        if e.get("actions_taken"):
            lines.append(f"   actions: {', '.join(e['actions_taken'])}")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_journal.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 5: Run full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~272 passed, 3 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/memory/journal.py tests/unit/test_journal.py
git commit -m "Add JSONL incident journal with tiered similarity lookup"
```

---

## Task 8: Renderer.proposed_action (TDD)

**Files:**

- Modify: `src/k8sense/render.py`
- Modify: `tests/unit/test_render.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_render.py`:

```python


def test_proposed_action_renders_command_and_hint(captured):
    renderer, console = captured
    renderer.proposed_action(
        "kubectl delete pod argocd-server-7d -n argocd",
        "allowlisted mutation surfaced for review in propose mode",
    )
    output = console.export_text()
    assert "propose mode" in output
    assert "kubectl delete pod argocd-server-7d -n argocd" in output
    assert "surfaced for review" in output


def test_proposed_action_without_hint(captured):
    renderer, console = captured
    renderer.proposed_action("kubectl cordon worker1", "")
    output = console.export_text()
    assert "kubectl cordon worker1" in output
```

- [ ] **Step 2: Run — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_render.py::test_proposed_action_renders_command_and_hint -v
```

Expected: `AttributeError: 'Renderer' object has no attribute 'proposed_action'`.

- [ ] **Step 3: Add the method**

In `src/k8sense/render.py`, add the new method to the `Renderer` class. Insert it after `subagent_dispatch` (the methods follow each other in the visual-language section):

```python
    def proposed_action(self, command: str, hint: str) -> None:
        """Render the propose-mode marker (yellow-bold)."""
        self.console.print(Text("⚠ propose mode — k8sense would execute:", style="bold yellow"))
        self.console.print(Text(f"    $ {command}", style="yellow"))
        if hint:
            self.console.print(Text(f"  {hint}", style="dim"))
```

- [ ] **Step 4: Run — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_render.py -v
```

Expected: all render tests pass (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/k8sense/render.py tests/unit/test_render.py
git commit -m "Add Renderer.proposed_action() for propose-mode marker"
```

---

## Task 9: CLI `--propose` / `--auto-fix` flags + doctor finding (TDD)

**Files:**

- Modify: `src/k8sense/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_cli.py`:

```python


def test_ask_accepts_propose_flag():
    parser = build_parser()
    ns = parser.parse_args(["ask", "--propose", "fix it"])
    assert ns.permission_mode_flag == "propose"
    assert ns.question == "fix it"


def test_ask_accepts_auto_fix_flag():
    parser = build_parser()
    ns = parser.parse_args(["ask", "--auto-fix", "fix it"])
    assert ns.permission_mode_flag == "auto-safe"


def test_ask_without_flag_has_no_mode_flag():
    parser = build_parser()
    ns = parser.parse_args(["ask", "just a question"])
    assert ns.permission_mode_flag is None


def test_doctor_finds_permission_mode_with_default(monkeypatch):
    monkeypatch.delenv("K8SENSE_PERMISSION_MODE", raising=False)
    findings = doctor_check()
    mode_findings = [f for f in findings if "permission_mode" in f.message]
    assert len(mode_findings) == 1
    assert "readonly" in mode_findings[0].message
    assert "default" in mode_findings[0].message


def test_doctor_reports_env_override(monkeypatch):
    monkeypatch.setenv("K8SENSE_PERMISSION_MODE", "auto-safe")
    findings = doctor_check()
    mode_findings = [f for f in findings if "permission_mode" in f.message]
    assert "auto-safe" in mode_findings[0].message
    assert "env" in mode_findings[0].message.lower()
```

- [ ] **Step 2: Run — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_cli.py -v
```

Expected: failures on the new tests.

- [ ] **Step 3: Modify `cli.py`**

Read the current `src/k8sense/cli.py` first to know its shape (subcommand structure, doctor_check, main).

Add the two flags to the `ask` subparser. Find the existing block:

```python
    ask = sub.add_parser("ask", help="ask a question about the cluster")
    ask.add_argument("question", help="natural-language question, e.g. 'why is pod X crashing?'")
```

Add right after the `ask.add_argument("question", ...)` line:

```python
    ask.add_argument(
        "--propose",
        action="store_const",
        const="propose",
        dest="permission_mode_flag",
        help="propose mutations instead of executing (prints copy-paste command)",
    )
    ask.add_argument(
        "--auto-fix",
        action="store_const",
        const="auto-safe",
        dest="permission_mode_flag",
        help="auto-execute whitelisted safe mutations",
    )
    ask.set_defaults(permission_mode_flag=None)
```

Update `doctor_check` to include a permission_mode finding. Find the existing function and add this at the end of the list construction (after the existing kubectl/kubeconfig/auth findings):

```python
    from k8sense.permissions import ENV_VAR, CONFIG_PATH, resolve

    env = os.environ.get(ENV_VAR)
    if env:
        try:
            mode = resolve()
            findings.append(Finding(ok=True, message=f"permission_mode = {mode.value} (from env {ENV_VAR})"))
        except Exception:
            findings.append(Finding(ok=False, message=f"permission_mode = invalid env {ENV_VAR}={env!r}"))
    elif CONFIG_PATH.exists():
        try:
            mode = resolve()
            findings.append(Finding(ok=True, message=f"permission_mode = {mode.value} (from {CONFIG_PATH})"))
        except Exception:
            findings.append(Finding(ok=False, message=f"permission_mode = invalid value in {CONFIG_PATH}"))
    else:
        findings.append(Finding(ok=True, message="permission_mode = readonly (default; no flag, env, or config override)"))
```

In `main`, modify the `ask` branch to pass the resolved mode through to `run_ask`. Find:

```python
    if ns.command == "ask":
        try:
            return asyncio.run(run_ask(ns.question, renderer))
        ...
```

Change the `run_ask` call to:

```python
    if ns.command == "ask":
        from k8sense.permissions import resolve
        try:
            mode = resolve(flag_value=ns.permission_mode_flag)
        except ValueError as exc:
            renderer.error(str(exc))
            return 1
        try:
            return asyncio.run(run_ask(ns.question, renderer, mode=mode))
        ...
```

(The `run_ask` signature gets the new `mode` parameter in Task 10 — at this point the import will fail, which is fine. We'll wire it in Task 10. **Important:** don't actually run pytest with this state — the import would error. Make Task 9's commit small and move to Task 10 immediately.)

Wait — to keep TDD discipline, let's add the `mode` parameter to `run_ask` as a no-op default first, so this task's tests pass. Modify Step 3 above:

Actually the cleanest approach: in this task, only update the parser + doctor (which the new tests cover). Don't touch `main`'s call to `run_ask` yet — leave it calling `run_ask(ns.question, renderer)` for now. Task 10 will modify both `agent.py` AND `cli.py:main` together to wire mode through.

So **remove** the "modify main's run_ask call" step from this task. Only the parser changes + doctor changes happen in Task 9.

- [ ] **Step 4: Run the tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_cli.py -v
```

Expected: all CLI tests pass.

- [ ] **Step 5: Run full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~277 passed, 3 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/k8sense/cli.py tests/unit/test_cli.py
git commit -m "Add --propose / --auto-fix flags and doctor permission_mode finding"
```

---

## Task 10: Wire hook + journal into agent.build_options + run_ask (TDD)

This is the integration task. It modifies `agent.py` to (a) accept a `mode` parameter on `build_options` and `run_ask`, (b) wire the PreToolUse hook, (c) capture tool_results alongside tool_calls, (d) load prior incidents at the start, and (e) append a journal entry at the end. The CLI's `main` is updated to pass the resolved mode.

**Files:**

- Modify: `src/k8sense/agent.py`
- Modify: `src/k8sense/cli.py`
- Modify: `tests/unit/test_agent_helpers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_agent_helpers.py`:

```python


def test_build_options_accepts_mode_and_wires_hook():
    from k8sense.permissions import PermissionMode
    options = build_options("SYS", model_id="claude-sonnet-4-6", mode=PermissionMode.AUTO_SAFE)
    hooks = getattr(options, "hooks", None)
    assert hooks is not None
    assert "PreToolUse" in hooks
    matchers = hooks["PreToolUse"]
    assert len(matchers) >= 1
    # The matcher should target the kubectl tool
    matcher = matchers[0]
    assert matcher.get("matcher") == "mcp__k8sense__kubectl" or getattr(matcher, "matcher", None) == "mcp__k8sense__kubectl"


def test_build_options_default_mode_is_readonly():
    from k8sense.permissions import PermissionMode
    # No mode kwarg → readonly by default
    options = build_options("SYS", model_id="claude-sonnet-4-6")
    # Hook is still attached
    hooks = getattr(options, "hooks", None)
    assert hooks is not None
    assert "PreToolUse" in hooks
```

- [ ] **Step 2: Run — expect failure**

Run:

```bash
.venv/bin/pytest tests/unit/test_agent_helpers.py -v
```

Expected: failures on the new tests — `build_options` doesn't take a `mode` parameter yet.

- [ ] **Step 3: Modify `agent.py`**

Read `src/k8sense/agent.py` to know its current shape (existing imports, `build_options`, `run_ask`, `_dispatch_message`).

Add new imports near the top, alongside the existing imports:

```python
from claude_agent_sdk import HookMatcher

from k8sense.hooks.pre_tool_use import build_pre_tool_use_hook
from k8sense.memory import journal as journal_module
from k8sense.memory.signature import extract as extract_signature, extract_text_hints
from k8sense.permissions import PermissionMode
```

Modify `build_options` signature and body to accept `mode` and `on_propose`:

```python
def build_options(
    system_prompt: str,
    model_id: str = DEFAULT_MODEL,
    mode: PermissionMode = PermissionMode.READONLY,
    on_propose=None,
) -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions via the shared tool registry + hook + subagents."""
    sdk_tools = [
        tool(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_model.model_json_schema(),
        )(spec.handler)
        for spec in all_tool_specs()
    ]
    server = create_sdk_mcp_server(name="k8sense", version="0.4.0", tools=sdk_tools)
    hook_cb = build_pre_tool_use_hook(mode, on_propose=on_propose)
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"k8sense": server},
        allowed_tools=[f"mcp__k8sense__{spec.name}" for spec in all_tool_specs()],
        agents={
            "event_triager": event_triager_definition,
            "log_investigator": log_investigator_definition,
            "metrics_analyst": metrics_analyst_definition,
        },
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="mcp__k8sense__kubectl", hooks=[hook_cb]),
            ],
        },
        model=model_id,
    )
```

Modify `run_ask` to accept `mode`, load prior incidents, capture tool_results, and append a journal entry at the end. Find the existing `run_ask` and replace it with:

```python
async def run_ask(
    question: str,
    renderer: Renderer,
    mode: PermissionMode = PermissionMode.READONLY,
    model_id: str | None = None,
) -> int:
    """Run a one-shot investigation under the given permission mode."""
    # 1. Pre-investigation: text-hint signature → similar prior incidents
    hints = extract_text_hints(question)
    prior_entries = journal_module.find_similar(hints, journal_module.load_all())
    prior_block = journal_module.format_for_prompt(prior_entries)

    try:
        system_prompt = await build_system_prompt()
    except RuntimeError as exc:
        renderer.error(str(exc))
        return 1

    user_message = question if not prior_block else f"{question}\n\n{prior_block}"

    options = build_options(
        system_prompt,
        model_id=model_id or os.environ.get("K8SENSE_MODEL", DEFAULT_MODEL),
        mode=mode,
        on_propose=lambda cmd, msg: renderer.proposed_action(cmd, msg),
    )

    # Capture for journal
    captured_tool_calls: list[dict] = []
    captured_tool_results: list[dict] = []
    final_text_parts: list[str] = []
    actions_taken: list[str] = []

    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_message)
        async for message in client.receive_response():
            _dispatch_message_with_capture(
                message, renderer,
                tool_calls_out=captured_tool_calls,
                tool_results_out=captured_tool_results,
                final_text_out=final_text_parts,
                actions_taken_out=actions_taken,
            )

    final_text = "".join(final_text_parts)
    signature = extract_signature(captured_tool_calls, captured_tool_results, final_text)

    try:
        journal_module.append_entry(
            question=question,
            final_text=final_text,
            tool_calls=captured_tool_calls,
            tool_results=captured_tool_results,
            signature=signature,
            actions_taken=actions_taken,
            mode=mode.value,
        )
    except OSError as exc:
        renderer.error(f"could not write journal entry: {exc}")

    return 0
```

(The existing `run_ask` had a `max_tool_calls` budget — preserve any such logic if it exists in your current file. The example above shows the load-bearing changes; merge them with the existing structure.)

Add a new `_dispatch_message_with_capture` helper just below the existing `_dispatch_message`. Find the existing `_dispatch_message`:

```python
def _dispatch_message(message, renderer):
    ...
```

Don't replace it — leave it for backwards compatibility. Add the new capturing variant:

```python
def _dispatch_message_with_capture(
    message,
    renderer: Renderer,
    *,
    tool_calls_out: list[dict],
    tool_results_out: list[dict],
    final_text_out: list[str],
    actions_taken_out: list[str],
) -> None:
    """Same dispatch as _dispatch_message but additionally captures tool calls,
    tool results, final-text fragments, and actions-taken into the lists passed in."""
    # The existing _dispatch_message logic likely uses isinstance checks on message
    # types (AssistantMessage / UserMessage / ResultMessage / ToolUseBlock / etc.)
    # Mirror that here, but for each ToolUseBlock append to tool_calls_out, for
    # each tool-result content block append to tool_results_out, and for each
    # AssistantMessage TextBlock / ResultMessage.result append to final_text_out.
    #
    # Important: when an allowlisted mutation is ALLOWED (hook returns {}), the
    # kubectl tool actually runs. The tool call's args go to tool_calls_out, and
    # when the hook allowed a `delete pod x` we record "kubectl delete pod x ..."
    # in actions_taken_out. The simplest implementation: any kubectl tool call
    # whose args[0] is in {"delete", "rollout", "cordon"} is considered an action.

    # Implementation sketch:
    from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock, UserMessage
    from claude_agent_sdk import TextBlock

    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                final_text_out.append(block.text)
                renderer.thinking(block.text)
            elif isinstance(block, ToolUseBlock):
                call = {"name": block.name, "input": block.input}
                tool_calls_out.append(call)
                # Render dispatch / tool call (mirror existing _dispatch_message)
                if block.name == "mcp__k8sense__Task":
                    # subagent dispatch (Phase 2)
                    pass
                else:
                    renderer.tool_call(block.name, block.input)
                # Track mutations as actions_taken
                if block.name == "mcp__k8sense__kubectl":
                    args = (block.input or {}).get("args", [])
                    if args and args[0] in {"delete", "rollout", "cordon", "drain"}:
                        actions_taken_out.append("kubectl " + " ".join(args))
    elif isinstance(message, UserMessage):
        # tool results come back as UserMessage content blocks; capture them
        for block in getattr(message, "content", []):
            if hasattr(block, "content"):
                # ToolResultBlock variant — content is a list of blocks
                for sub in (block.content or []):
                    if hasattr(sub, "text"):
                        tool_results_out.append({"text": sub.text})
    elif isinstance(message, ResultMessage):
        if message.result:
            final_text_out.append(message.result)
            renderer.final(message.result)
```

**Note for implementer:** the existing `_dispatch_message` already handles these block types — your real job is to mirror its logic while also appending to the four `*_out` lists. Read the existing function carefully and produce a parallel function that does both. Don't delete the original — the old eval runner may still use it. (You can refactor in a follow-up if you want.)

Modify `cli.py` to pass the mode through. In `main`, the `ask` branch:

```python
    if ns.command == "ask":
        from k8sense.permissions import resolve
        try:
            mode = resolve(flag_value=ns.permission_mode_flag)
        except ValueError as exc:
            renderer.error(str(exc))
            return 1
        try:
            return asyncio.run(run_ask(ns.question, renderer, mode=mode))
        except KeyboardInterrupt:
            renderer.error("interrupted")
            return 130
        except Exception as exc:
            renderer.error(f"{type(exc).__name__}: {exc}")
            return 1
```

(Keep any existing exception handling; the load-bearing addition is the `resolve()` call and the `mode=mode` kwarg.)

- [ ] **Step 4: Run tests — expect pass**

Run:

```bash
.venv/bin/pytest tests/unit/test_agent_helpers.py -v
```

Expected: all agent_helpers tests pass.

- [ ] **Step 5: Run full suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~279 passed, 3 skipped. (No new tests beyond the 2 from Step 1; the rest is integration code exercised by the smoke test in Task 12 and by the eval suite expansion in Task 11.)

- [ ] **Step 6: Live sanity check (optional)**

Run:

```bash
.venv/bin/k8sense ask "list every namespace, briefly" 2>&1 | tail -10
```

Expected: a coherent answer. This verifies the integration didn't break the in-process path.

Also confirm a journal entry was written:

```bash
test -f ~/.k8sense/journal/incidents.jsonl && wc -l ~/.k8sense/journal/incidents.jsonl
```

Expected: file exists with at least 1 line.

- [ ] **Step 7: Commit**

```bash
git add src/k8sense/agent.py src/k8sense/cli.py tests/unit/test_agent_helpers.py
git commit -m "Wire hook + journal + mode into run_ask end-to-end"
```

---

## Task 11: Eval dataset expansion + runner mode plumbing

**Files:**

- Modify: `evals/runner.py`
- Modify: `evals/dataset.jsonl`

- [ ] **Step 1: Add `permission_mode` field to `EvalCase`**

In `evals/runner.py`, find the `EvalCase` dataclass:

```python
@dataclass
class EvalCase:
    id: str
    question: str
    fingerprints: list[dict[str, Any]]
```

Add `permission_mode`:

```python
@dataclass
class EvalCase:
    id: str
    question: str
    fingerprints: list[dict[str, Any]]
    permission_mode: str = "readonly"
```

- [ ] **Step 2: Pass mode through in load_dataset and \_run_one_case**

In `load_dataset`, when constructing `EvalCase`, include the new field:

```python
def load_dataset(path: Path) -> list[EvalCase]:
    cases = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        cases.append(EvalCase(
            id=data["id"],
            question=data["question"],
            fingerprints=data["fingerprints"],
            permission_mode=data.get("permission_mode", "readonly"),
        ))
    return cases
```

In `_run_one_case`, pass the mode through to `run_ask`:

```python
async def _run_one_case(case: EvalCase) -> EvalResult:
    from k8sense.agent import run_ask
    from k8sense.permissions import PermissionMode
    from k8sense.render import Renderer
    from rich.console import Console

    console = Console(file=open(os.devnull, "w"), force_terminal=False)
    renderer = Renderer(console=console)
    mode = PermissionMode(case.permission_mode)
    exit_code = await run_ask(case.question, renderer, mode=mode)
    # ... existing logic to scrape tool_calls and final_text from the captured console ...
```

(The exact `_run_one_case` body depends on its current shape. The key change is constructing the `PermissionMode` from the case field and passing it to `run_ask`. If the existing runner uses a different mechanism — e.g. direct SDK invocation — adjust to match.)

- [ ] **Step 3: Append three new dataset entries**

Append to `evals/dataset.jsonl`:

```jsonl
{"id": "mutation-blocked-readonly", "question": "the argocd-server pod is OOMKilled; restart it", "permission_mode": "readonly", "fingerprints": [{"type": "substring", "value": "deny"}]}
{"id": "mutation-proposed", "question": "the argocd-server pod is OOMKilled; restart it", "permission_mode": "propose", "fingerprints": [{"type": "substring", "value": "kubectl"}]}
{"id": "mutation-executed-autofix", "question": "the argocd-server pod is OOMKilled; restart it", "permission_mode": "auto-safe", "fingerprints": [{"type": "tool_called", "value": "kubectl"}]}
```

(These are deliberately loose-fingerprinted because the model's exact phrasing is hard to pin. Once we run them live and see the actual outputs, Phase 4.1 can tighten with the patterns we observed — same playbook as Phase 2's eval-tightening pattern.)

- [ ] **Step 4: Verify the dataset loads**

Run:

```bash
.venv/bin/python -c "from pathlib import Path; from evals.runner import load_dataset; cases = load_dataset(Path('evals/dataset.jsonl')); print(f'loaded {len(cases)} cases'); print([(c.id, c.permission_mode) for c in cases][-3:])"
```

Expected: `loaded 18 cases` and the last three entries show their permission modes.

- [ ] **Step 5: Run full suite (no regressions)**

Run:

```bash
.venv/bin/pytest -v
```

Expected: still ~279 passed.

- [ ] **Step 6: Commit**

```bash
git add evals/runner.py evals/dataset.jsonl
git commit -m "Expand eval suite with 3 mutation-path entries + per-case permission_mode"
```

---

## Task 12: Smoke test — propose mode end-to-end

**Files:**

- Create: `tests/smoke/test_propose_mode.py`

- [ ] **Step 1: Write the smoke test**

Create `tests/smoke/test_propose_mode.py`:

```python
"""End-to-end smoke: run k8sense ask --propose against the real cluster.

Verifies:
- The agent considers a mutation
- The hook intercepts and produces the propose-mode marker
- No actual kubectl mutation runs

Run with:
    K8SENSE_ALLOW_API=1 pytest -m smoke tests/smoke/test_propose_mode.py -v -s
"""
import asyncio
import os

import pytest
from rich.console import Console

from k8sense.agent import run_ask
from k8sense.permissions import PermissionMode
from k8sense.render import Renderer


@pytest.mark.smoke
def test_propose_mode_marker_appears_and_no_mutation_runs():
    if not os.environ.get("K8SENSE_ALLOW_API"):
        pytest.skip("smoke test requires K8SENSE_ALLOW_API=1")

    console = Console(record=True, force_terminal=False, width=120)
    renderer = Renderer(console=console)
    exit_code = asyncio.run(
        run_ask(
            "the argocd-server pod is OOMKilled; restart it by deleting the pod",
            renderer,
            mode=PermissionMode.PROPOSE,
        )
    )
    output = console.export_text()

    assert exit_code == 0, f"agent exited {exit_code}\n{output}"
    # The propose marker (or its substring) must appear
    assert "propose" in output.lower(), f"propose marker missing from output:\n{output}"
    # The agent should have at least mentioned the kubectl command
    assert "kubectl" in output.lower()
```

- [ ] **Step 2: Verify default suite skips it**

Run:

```bash
.venv/bin/pytest -v
```

Expected: 4 skipped (the 3 prior smoke tests + this new one).

- [ ] **Step 3: Run smoke manually**

Run:

```bash
K8SENSE_ALLOW_API=1 .venv/bin/pytest -m smoke tests/smoke/test_propose_mode.py -v -s 2>&1 | tail -20
```

Expected: the smoke test passes. If the model's exact output differs from what the assertions expect (e.g. the model says "I'd run `kubectl delete pod`" but doesn't produce the propose marker because it didn't actually invoke the tool), report DONE_WITH_CONCERNS — the assertion may need loosening or the prompt may need a nudge. This is exactly the kind of Phase-4.1 follow-up we expect.

- [ ] **Step 4: Commit**

```bash
git add tests/smoke/test_propose_mode.py
git commit -m "Add propose-mode smoke test"
```

---

## Task 13: README polish + Phase 4 tag

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Update the README's phase table**

Read `README.md` to know the table's exact current format (the linter has touched it before).

Find the row that currently says `4-5` and replace it with two rows so Phase 4 has its own entry:

```markdown
| **4** | [`phase-4`](#phase-4--hooks-permission-modes-incident-journal) | `--auto-fix` / `--propose` modes + incident journal | PreToolUse hooks, permission modes, persistent memory |
| 5 | — | Sentinel daemon | (See spec.) |
```

(Format the table cell widths to match the existing rows after editing.)

- [ ] **Step 2: Add the Phase 4 section**

Insert AFTER the Phase 3 section and BEFORE the `## Run the eval suite` heading:

````markdown
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
````

- [ ] **Step 3: Update the architecture tree**

Find the existing architecture tree (in fenced code) and add the new modules in place:

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
```

- [ ] **Step 4: Update the test count line**

Find the current line ("Current count: **200 unit tests** + 3 smoke (skipped by default)") and update to roughly:

"Current count: **~280 unit tests** + 4 smoke (skipped by default)"

(Exact number depends on how many parametrize cases land — refresh by running `pytest --collect-only | tail`.)

- [ ] **Step 5: Run the full suite once more**

Run:

```bash
.venv/bin/pytest -v
```

Expected: ~279 unit tests pass, 4 smoke skipped.

- [ ] **Step 6: Commit README**

```bash
git add README.md
git commit -m "Phase 4 complete: ship hooks + permission modes + incident journal"
```

- [ ] **Step 7: Tag and push**

```bash
git tag -a phase-4 -m "Phase 4: PreToolUse hook + permission modes + incident journal"
git push origin main
git push origin phase-4
# Create the phase/4 branch like the other phases for GitHub UI discoverability
git branch phase/4 phase-4
git push origin phase/4
git log --oneline -5
```

Expected: `phase-4` tag and `phase/4` branch both pushed to GitHub.

---

## Phase 4 acceptance checklist

After all tasks complete, verify:

- [ ] `k8sense ask "list every namespace"` still works (Phase 1-3 behaviour preserved).
- [ ] `k8sense ask --auto-fix "delete the failed argocd-server pod"` actually deletes an unhealthy argocd pod when one exists.
- [ ] `k8sense ask --propose "delete the failed argocd-server pod"` prints the propose-mode marker and does NOT execute the kubectl mutation.
- [ ] `k8sense doctor` reports the active permission mode and its source.
- [ ] `pytest` passes (all 207 prior + ~80 new = ~280 unit tests).
- [ ] `K8SENSE_ALLOW_API=1 pytest -m smoke` runs all 4 smoke tests against the real cluster.
- [ ] After running a few `k8sense ask` invocations, `~/.k8sense/journal/incidents.jsonl` accumulates one valid entry per invocation.
- [ ] Asking about an issue that was investigated before causes the prior-incidents block to appear in the streamed output (proves journal injection works).
- [ ] No mocking of kubectl, Prometheus, the SDK, or the MCP server anywhere in the test suite.

If all boxes are ticked, Phase 4 is shippable and we can move to Phase 5 (sentinel daemon).
