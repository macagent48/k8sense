"""Internal validation helpers shared across mcp_server modules."""

from __future__ import annotations

import re

# DNS-1123 label: lowercase alphanumeric and hyphens, 1-63 chars,
# must start and end with alphanumeric (rejects all-hyphens strings like "--all").
NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def is_valid_namespace(ns: str) -> bool:
    """Return True iff `ns` is a valid DNS-1123 label."""
    return bool(NAMESPACE_RE.match(ns))


def validate_namespace(ns: str) -> None:
    """Raise ValueError if `ns` is not a valid DNS-1123 label."""
    if not is_valid_namespace(ns):
        raise ValueError(f"invalid namespace: {ns!r}")
