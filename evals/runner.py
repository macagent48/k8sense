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
                value
                in " ".join(str(a) for a in (tc.get("input") or {}).get("args", []))
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
        cases.append(
            EvalCase(
                id=data["id"],
                question=data["question"],
                fingerprints=data["fingerprints"],
            )
        )
    return cases
