"""Core agent loop: assembles SDK options, drives the streaming receive loop."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from k8sense.hooks.pre_tool_use import build_pre_tool_use_hook
from k8sense.memory import journal as journal_module
from k8sense.memory.signature import extract as extract_signature
from k8sense.memory.signature import extract_text_hints
from k8sense.permissions import PermissionMode
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

_KUBECTL_MUTATION_VERBS = {"delete", "rollout", "cordon", "drain"}


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


async def run_ask(
    question: str,
    renderer: Renderer,
    model_id: str | None = None,
    mode: PermissionMode = PermissionMode.READONLY,
) -> int:
    """Run a one-shot investigation. Returns the process exit code."""
    try:
        system_prompt = await build_system_prompt(mode=mode)
    except RuntimeError as exc:
        renderer.error(str(exc))
        return 1

    # Load prior incidents for context injection
    hints_sig = extract_text_hints(question)
    prior_entries = journal_module.load_all()
    similar = journal_module.find_similar(hints_sig, prior_entries)
    prior_block = journal_module.format_for_prompt(similar)

    augmented_question = question
    if prior_block:
        augmented_question = f"{question}\n\n{prior_block}"

    options = build_options(
        system_prompt,
        model_id=model_id or os.environ.get("K8SENSE_MODEL", DEFAULT_MODEL),
        mode=mode,
        on_propose=lambda c, m: renderer.proposed_action(c, m),
    )
    budget = ToolBudget()

    # Capture lists for journal
    captured_tool_calls: list[dict] = []
    captured_tool_results: list[dict] = []
    final_text_parts: list[str] = []
    mutations_attempted: list[str] = []
    mutations_executed: list[str] = []

    async with ClaudeSDKClient(options=options) as client:
        await client.query(augmented_question)
        async for message in client.receive_response():
            should_continue = _dispatch_message_with_capture(
                message,
                renderer,
                budget,
                captured_tool_calls,
                captured_tool_results,
                final_text_parts,
                mutations_attempted,
                mutations_executed,
            )
            if not should_continue:
                return 1

    final_text = "".join(final_text_parts)
    signature = extract_signature(
        tool_calls=captured_tool_calls,
        tool_results=captured_tool_results,
        final_text=final_text,
    )
    try:
        journal_module.append_entry(
            question=question,
            final_text=final_text,
            tool_calls=captured_tool_calls,
            tool_results=captured_tool_results,
            signature=signature,
            mutations_attempted=mutations_attempted,
            mutations_executed=mutations_executed,
            mode=mode.value,
        )
    except OSError as exc:
        renderer.error(f"could not write journal entry: {exc}")

    return 0


_DENIED_SIGNALS = frozenset({"Proposed", "blocked", "deny", "permissionDecision"})


def _is_denied_result(text: str) -> bool:
    """Heuristic: return True if the tool result text signals the call was blocked.

    Looks for substrings produced by the hook when it intercepts a mutation:
    "Proposed (not executed)", "blocked", "deny", "permissionDecision".
    A result that contains none of these is assumed to have actually run.
    """
    lower = text.lower()
    return (
        "proposed" in lower
        or "blocked" in lower
        or "deny" in lower
        or "permissiondecision" in lower
    )


def _dispatch_message(message, renderer: Renderer, budget: ToolBudget) -> bool:
    """Route a streamed message to the renderer. Return False to abort the loop.

    This version is kept for compatibility with the eval runner (doesn't capture).
    """
    return _dispatch_message_with_capture(message, renderer, budget, [], [], [], [], [])


def _dispatch_message_with_capture(
    message,
    renderer: Renderer,
    budget: ToolBudget,
    captured_tool_calls: list[dict],
    captured_tool_results: list[dict],
    final_text_parts: list[str],
    mutations_attempted_out: list[str],
    mutations_executed_out: list[str],
) -> bool:
    """Route a streamed message to the renderer and capture data for the journal.

    Tracks mutations_attempted (every mutation verb the agent tried to call) and
    mutations_executed (only those whose tool result did not contain a deny/block
    signal from the hook).

    Return False to abort the loop.
    """
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                renderer.thinking(block.text)
                final_text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                if not budget.charge():
                    renderer.error(f"hit max tool calls ({budget.limit})")
                    return False
                if is_subagent_dispatch(block.name):
                    name, brief = extract_subagent_dispatch(block.input or {})
                    renderer.subagent_dispatch(name, brief)
                else:
                    renderer.tool_call(block.name, block.input)
                    captured_tool_calls.append(
                        {"name": block.name, "input": block.input}
                    )
                    # Track kubectl mutation attempts for the journal
                    if block.name == "mcp__k8sense__kubectl":
                        args = (block.input or {}).get("args", [])
                        if args and args[0] in _KUBECTL_MUTATION_VERBS:
                            mutations_attempted_out.append("kubectl " + " ".join(args))
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
            captured_tool_results.append({"text": text})
            # Promote the most recent attempted mutation to executed if the
            # result doesn't contain a deny/block signal from the hook.
            # Heuristic: pair by position — the last attempted command that
            # hasn't yet been resolved gets promoted when this result arrives.
            if mutations_attempted_out and not _is_denied_result(text):
                # Only promote if this result looks like a kubectl mutation result
                # (contains exit_code line or stdout from kubectl_handler).
                if "exit_code=" in text or stdout:
                    # Find the first unresolved attempt (simple FIFO heuristic)
                    pending = len(mutations_attempted_out) - len(mutations_executed_out)
                    if pending > 0:
                        cmd = mutations_attempted_out[len(mutations_executed_out)]
                        mutations_executed_out.append(cmd)
        return True

    if isinstance(message, ResultMessage):
        result_text = getattr(message, "result", None) or ""
        if result_text:
            renderer.final(result_text)
            final_text_parts.append(result_text)
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
