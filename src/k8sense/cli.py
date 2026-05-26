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
    parser = argparse.ArgumentParser(
        prog="k8sense", description="Homelab k3s SRE agent"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="ask a question about the cluster")
    ask.add_argument(
        "question", help="natural-language question, e.g. 'why is pod X crashing?'"
    )
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

    sub.add_parser("doctor", help="check the local environment")

    sub.add_parser("mcp", help="run k8sense as a stdio MCP server")

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
        findings.append(
            Finding(ok=False, message=f"kubeconfig not found at {kubeconfig}")
        )

    if os.environ.get("ANTHROPIC_API_KEY"):
        findings.append(
            Finding(ok=True, message="ANTHROPIC_API_KEY is set (API billing)")
        )
    elif shutil.which("claude"):
        findings.append(
            Finding(ok=True, message="claude CLI on PATH (OAuth via Claude Code)")
        )
    else:
        findings.append(
            Finding(
                ok=False,
                message="no auth: set ANTHROPIC_API_KEY or install the claude CLI",
            )
        )

    from k8sense.permissions import ENV_VAR, CONFIG_PATH, resolve

    env = os.environ.get(ENV_VAR)
    if env:
        try:
            mode = resolve()
            findings.append(
                Finding(
                    ok=True,
                    message=f"permission_mode = {mode.value} (from env {ENV_VAR})",
                )
            )
        except Exception:
            findings.append(
                Finding(
                    ok=False, message=f"permission_mode = invalid env {ENV_VAR}={env!r}"
                )
            )
    elif CONFIG_PATH.exists():
        try:
            mode = resolve()
            findings.append(
                Finding(
                    ok=True,
                    message=f"permission_mode = {mode.value} (from {CONFIG_PATH})",
                )
            )
        except Exception:
            findings.append(
                Finding(
                    ok=False,
                    message=f"permission_mode = invalid value in {CONFIG_PATH}",
                )
            )
    else:
        findings.append(
            Finding(
                ok=True,
                message="permission_mode = readonly (default; no flag, env, or config override)",
            )
        )

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
        try:
            return asyncio.run(run_ask(ns.question, renderer))
        except KeyboardInterrupt:
            renderer.error("interrupted")
            return 130
        except Exception as exc:
            # Spec: SDK / network / auth errors bubble up as a one-line error, exit 1.
            renderer.error(f"{type(exc).__name__}: {exc}")
            return 1

    if ns.command == "mcp":
        from k8sense.mcp_server.server import run_stdio

        try:
            asyncio.run(run_stdio())
        except KeyboardInterrupt:
            return 130
        return 0

    # Unreachable: argparse enforces required=True on subparsers.
    raise RuntimeError(f"unexpected command: {ns.command}")


if __name__ == "__main__":
    sys.exit(main())
