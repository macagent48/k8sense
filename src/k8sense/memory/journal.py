"""Incident journal: append-only JSONL with tiered similarity lookup."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from k8sense.memory.signature import Signature

JOURNAL_DIR = Path.home() / ".k8sense" / "journal"
JOURNAL_PATH = JOURNAL_DIR / "incidents.jsonl"


def append_entry(
    question: str,
    final_text: str,
    tool_calls: list[dict],
    tool_results: list[dict],
    signature: Signature,
    actions_taken: list[str],
    mode: str,
    severity: str = "info",
) -> None:
    """Append one investigation to the journal. Idempotent in directory creation."""
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "summary": final_text[:500],
        "tools_called": tool_calls,
        "tool_results": tool_results,
        "resolution": final_text[-500:] if len(final_text) > 500 else final_text,
        "signature": {
            "kind": signature.kind,
            "namespace": signature.namespace,
            "name": signature.name,
            "reason": signature.reason,
        },
        "actions_taken": actions_taken,
        "mode": mode,
        "severity": severity,
    }
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_all(path: Path | None = None) -> list[dict[str, Any]]:
    """Load every journal entry. Skips malformed lines silently."""
    p = path if path is not None else JOURNAL_PATH
    if not p.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def find_similar(
    signature: Signature,
    entries: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Tiered lookup. Up to `limit` entries, most recent first within each tier.

    Tier 1: exact (kind, namespace, name, reason)
    Tier 2: (kind, namespace, reason) — ignore name
    Tier 3: (kind, reason)             — ignore namespace + name
    """
    if signature.is_empty():
        return []

    matched_indices: set[int] = set()
    out: list[dict[str, Any]] = []

    def _collect_tier(predicate) -> int:
        """Collect matching entries for one tier. Returns count of new entries added."""
        before = len(out)
        for i in range(len(entries) - 1, -1, -1):
            if len(out) >= limit:
                break
            if i in matched_indices:
                continue
            sig = entries[i].get("signature", {})
            if predicate(sig):
                matched_indices.add(i)
                out.append(entries[i])
        return len(out) - before

    # Tier 1: exact match — if it finds anything, stop here.
    tier1_hits = _collect_tier(
        lambda s: (
            s.get("kind") == signature.kind
            and s.get("namespace") == signature.namespace
            and s.get("name") == signature.name
            and s.get("reason") == signature.reason
        )
    )
    if tier1_hits:
        return out

    # Tier 2: same kind + namespace + reason (ignore name).
    _collect_tier(
        lambda s: (
            s.get("kind") == signature.kind
            and s.get("namespace") == signature.namespace
            and s.get("reason") == signature.reason
        )
    )
    # Tier 3: same kind + reason (ignore namespace + name) — fills remaining slots.
    _collect_tier(
        lambda s: (
            s.get("kind") == signature.kind and s.get("reason") == signature.reason
        )
    )

    return out


def format_for_prompt(similar_entries: list[dict[str, Any]]) -> str:
    """Render similar incidents as a markdown block for injection into the user message."""
    if not similar_entries:
        return ""
    lines = ["## Prior incidents (most recent first)", ""]
    for i, e in enumerate(similar_entries, 1):
        sig = e.get("signature", {})
        lines.append(
            f"{i}. [{e.get('timestamp', '')[:10]}] "
            f"{sig.get('kind')}/{sig.get('namespace')}/{sig.get('name')} "
            f"— reason: {sig.get('reason')}"
        )
        resolution = e.get("resolution") or "<no summary>"
        lines.append(f"   resolved: {resolution[:200]}")
        if e.get("actions_taken"):
            lines.append(f"   actions: {', '.join(e['actions_taken'])}")
        lines.append("")
    return "\n".join(lines)
