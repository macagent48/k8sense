"""Core agent loop: assembles SDK options, drives the streaming receive loop."""

from __future__ import annotations

import os
import re
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

_EXIT_CODE_RE = re.compile(r"^exit_code=(-?\d+)", re.MULTILINE)


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


def build_options(
    system_prompt: str, model_id: str = DEFAULT_MODEL
) -> ClaudeAgentOptions:
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
        # Tool results come back as UserMessage content. Render the result panel
        # with the real exit code parsed from the handler's formatted text.
        for block in getattr(message, "content", []) or []:
            content_block = getattr(block, "content", None)
            if content_block is None:
                continue
            text = _extract_tool_result_text(content_block)
            exit_code = parse_exit_code(text)
            renderer.tool_result(stdout=text, stderr="", exit_code=exit_code)
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
