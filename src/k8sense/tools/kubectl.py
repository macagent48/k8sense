"""kubectl tool: read-only verb allowlist + subprocess wrapper."""

from __future__ import annotations

ALLOWED_VERBS: frozenset[str] = frozenset(
    {"get", "describe", "logs", "top", "events", "version"}
)


def is_allowed(args: list[str]) -> bool:
    """Return True if the first positional arg is an allowed read-only verb."""
    if not args:
        return False
    # Comparison is case-sensitive; callers must lowercase verbs themselves.
    return args[0] in ALLOWED_VERBS
