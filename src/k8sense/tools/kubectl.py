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
    # Comparison is case-sensitive; callers must lowercase verbs themselves.
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

    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {
            "stdout": "",
            "stderr": "kubectl not found on PATH",
            "exit_code": -1,
        }
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
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
