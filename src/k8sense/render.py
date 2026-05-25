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
            command = "kubectl" + (" " + " ".join(args) if args else "")
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
        self.console.print(
            Panel(Markdown(text), border_style="bold blue", padding=(0, 1))
        )

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
