"""Core agent loop: assembles SDK options, drives the streaming receive loop."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from k8sense.prompts.system import build_system_prompt
from k8sense.render import Renderer
from k8sense.subagents import (
    event_triager_definition,
    log_investigator_definition,
    metrics_analyst_definition,
)
from k8sense.tools.registry import all_tool_specs

MAX_TOOL_CALLS = 20
DEFAULT_MODEL = "claude-sonnet-4-6"

_EXIT_CODE_RE = re.compile(r"^exit_code=(-?\d+)", re.MULTILINE)

SUBAGENT_DISPATCH_TOOL_NAME = "Agent"  # SDK names the dispatch primitive "Agent"
_BRIEF_MAX_LEN = 120


def is_subagent_dispatch(tool_name: str) -> bool:
    """Return True if the tool name signals a subagent dispatch (not a normal tool call)."""
    return tool_name == SUBAGENT_DISPATCH_TOOL_NAME


def extract_subagent_dispatch(tool_input: dict) -> tuple[str, str]:
    """Pull (subagent_name, brief description) from a Task tool call input.

    Brief prefers `description` over `prompt`, truncated to a renderer-friendly length.
    """
    name = tool_input.get("subagent_type", "<unknown>")
    brief = tool_input.get("description") or tool_input.get("prompt") or ""
    if len(brief) > _BRIEF_MAX_LEN:
        brief = brief[: _BRIEF_MAX_LEN - 1] + "…"
    return name, brief


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


def parse_exit_code(text: str) -> int:
    """Extract the kubectl exit code from a handler-rendered tool result.

    The kubectl_handler emits text containing a line like 'exit_code=0'. This
    function pulls that number out so the renderer can colour panels correctly.
    Returns 0 if no match (i.e. the text isn't from kubectl_handler at all).
    """
    match = _EXIT_CODE_RE.search(text)
    return int(match.group(1)) if match else 0


def parse_handler_envelope(text: str) -> tuple[int, str, str]:
    """Parse the kubectl_handler envelope into (exit_code, stdout, stderr).

    Format produced by kubectl_handler:
        $ kubectl <args>
        exit_code=<n>
        --- stdout ---
        <stdout>
        [--- stderr ---
        <stderr>]

    Falls back gracefully: if the envelope isn't recognised, returns
    (0, text, "") so the agent loop never crashes on unexpected content.
    """
    exit_code = parse_exit_code(text)

    stdout_marker = "--- stdout ---\n"
    stderr_marker = "\n--- stderr ---\n"

    stdout = ""
    stderr = ""

    if stdout_marker in text:
        after_stdout = text.split(stdout_marker, 1)[1]
        if stderr_marker in after_stdout:
            stdout, _, stderr = after_stdout.partition(stderr_marker)
        else:
            stdout = after_stdout
    else:
        # Not a kubectl_handler envelope — return the whole thing as stdout
        stdout = text

    return exit_code, stdout.rstrip("\n"), stderr.rstrip("\n")


def build_options(
    system_prompt: str, model_id: str = DEFAULT_MODEL
) -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions via the shared tool registry."""
    sdk_tools = [
        tool(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_model.model_json_schema(),
        )(spec.handler)
        for spec in all_tool_specs()
    ]
    server = create_sdk_mcp_server(name="k8sense", version="0.3.0", tools=sdk_tools)
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"k8sense": server},
        allowed_tools=[f"mcp__k8sense__{spec.name}" for spec in all_tool_specs()],
        agents={
            "event_triager": event_triager_definition,
            "log_investigator": log_investigator_definition,
            "metrics_analyst": metrics_analyst_definition,
        },
        model=model_id,
    )


async def run_ask(
    question: str, renderer: Renderer, model_id: str | None = None
) -> int:
    """Run a one-shot investigation. Returns the process exit code."""
    try:
        system_prompt = await build_system_prompt()
    except RuntimeError as exc:
        renderer.error(str(exc))
        return 1

    options = build_options(
        system_prompt,
        model_id=model_id or os.environ.get("K8SENSE_MODEL", DEFAULT_MODEL),
    )
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
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                renderer.thinking(block.text)
            elif isinstance(block, ToolUseBlock):
                if not budget.charge():
                    renderer.error(f"hit max tool calls ({budget.limit})")
                    return False
                if is_subagent_dispatch(block.name):
                    name, brief = extract_subagent_dispatch(block.input or {})
                    renderer.subagent_dispatch(name, brief)
                else:
                    renderer.tool_call(block.name, block.input)
        return True

    if isinstance(message, UserMessage):
        # Tool results come back as UserMessage content. Parse the handler
        # envelope so stdout/stderr are separated and the exit code isn't
        # rendered twice in the panel.
        for block in getattr(message, "content", []) or []:
            content_block = getattr(block, "content", None)
            if content_block is None:
                continue
            text = _extract_tool_result_text(content_block)
            exit_code, stdout, stderr = parse_handler_envelope(text)
            renderer.tool_result(stdout=stdout, stderr=stderr, exit_code=exit_code)
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
            text = (
                item.get("text")
                if isinstance(item, dict)
                else getattr(item, "text", None)
            )
            if text:
                parts.append(text)
        return "\n".join(parts)
    return str(content)
