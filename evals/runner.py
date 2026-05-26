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
    permission_mode: str = "readonly"


@dataclass
class EvalResult:
    final_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def score_fingerprints(case: EvalCase, result: EvalResult) -> tuple[bool, list[str]]:
    """Return (all_pass, failure_messages)."""
    failures: list[str] = []
    for fp in case.fingerprints:
        kind = fp["type"]
        value = fp.get("value")
        if kind == "substring":
            if value not in result.final_text:
                failures.append(f"substring '{value}' not found in final text")
        elif kind == "regex":
            try:
                matched = re.search(value, result.final_text) is not None
            except re.error as exc:
                failures.append(f"regex '{value}' is invalid: {exc}")
                continue
            if not matched:
                failures.append(f"regex '{value}' did not match final text")
        elif kind == "tool_called":
            # Substring match because SDK MCP tools surface as "mcp__<server>__<tool>",
            # not the short name. Dataset entries use short names like "kubectl".
            if not any(value in (tc.get("name") or "") for tc in result.tool_calls):
                failures.append(f"tool '{value}' was never called")
        elif kind == "tool_args_contains":
            ok = any(
                value
                in " ".join(str(a) for a in (tc.get("input") or {}).get("args", []))
                for tc in result.tool_calls
            )
            if not ok:
                failures.append(f"no tool call args contained '{value}'")
        elif kind == "subagent_called":
            ok = any(
                tc.get("name") == "Agent"
                and ((tc.get("input") or {}).get("subagent_type") == value)
                for tc in result.tool_calls
            )
            if not ok:
                failures.append(f"subagent '{value}' was never dispatched")
        elif kind == "subagent_not_called":
            invoked = any(
                tc.get("name") == "Agent"
                and ((tc.get("input") or {}).get("subagent_type") == value)
                for tc in result.tool_calls
            )
            if invoked:
                failures.append(
                    f"subagent '{value}' was dispatched but shouldn't have been"
                )
        else:
            failures.append(f"unknown fingerprint type: {kind}")
    return len(failures) == 0, failures


def load_dataset(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        data = json.loads(line)
        cases.append(
            EvalCase(
                id=data["id"],
                question=data["question"],
                fingerprints=data["fingerprints"],
                permission_mode=data.get("permission_mode", "readonly"),
            )
        )
    return cases


# --- live driver ---------------------------------------------------------


async def _run_one_case(case: EvalCase) -> EvalResult:
    """Run a single question against the real agent and capture result + tool calls."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )

    from k8sense.agent import build_options
    from k8sense.permissions import PermissionMode
    from k8sense.prompts.system import build_system_prompt

    system_prompt = await build_system_prompt()
    mode = PermissionMode(case.permission_mode)
    options = build_options(system_prompt, mode=mode)
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
    import argparse

    parser = argparse.ArgumentParser(description="Run k8sense evals")
    parser.add_argument("--dataset", default="evals/dataset.jsonl")
    parser.add_argument("--report", default="evals/report.md")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    cases = load_dataset(dataset_path)
    rows: list[str] = ["| id | pass | failures |", "| --- | --- | --- |"]
    passed = 0

    for case in cases:
        try:
            result = await _run_one_case(case)
        except Exception as exc:
            ok = False
            failures = [f"runner crashed: {type(exc).__name__}: {exc}"]
        else:
            ok, failures = score_fingerprints(case, result)
        passed += int(ok)
        rows.append(
            f"| {case.id} | {'✓' if ok else '✗'} | "
            f"{'<br>'.join(failures) if failures else ''} |"
        )

    report = [
        "# k8sense eval report",
        "",
        f"**{passed}/{len(cases)} passed**",
        "",
        *rows,
    ]
    Path(args.report).write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"{passed}/{len(cases)} passed — report written to {args.report}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    import asyncio as _asyncio

    raise SystemExit(_asyncio.run(_amain()))
