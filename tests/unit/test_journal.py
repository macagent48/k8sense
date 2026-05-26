"""memory.journal: append, load, tiered similarity lookup, prompt formatting."""


import pytest

from k8sense.memory.journal import (
    append_entry,
    find_similar,
    format_for_prompt,
    load_all,
)
from k8sense.memory.signature import Signature


@pytest.fixture
def journal_path(tmp_path, monkeypatch):
    path = tmp_path / "incidents.jsonl"
    monkeypatch.setattr("k8sense.memory.journal.JOURNAL_DIR", tmp_path)
    monkeypatch.setattr("k8sense.memory.journal.JOURNAL_PATH", path)
    return path


def test_load_all_returns_empty_when_file_absent(journal_path):
    assert load_all() == []


def test_append_entry_creates_directory_and_file(journal_path):
    sig = Signature(kind="Pod", namespace="argocd", name="x", reason="OOMKilled")
    append_entry(
        question="why?",
        final_text="OOMKilled",
        tool_calls=[],
        tool_results=[],
        signature=sig,
        actions_taken=[],
        mode="readonly",
    )
    assert journal_path.exists()
    entries = load_all()
    assert len(entries) == 1
    assert entries[0]["signature"] == {
        "kind": "Pod",
        "namespace": "argocd",
        "name": "x",
        "reason": "OOMKilled",
    }
    assert entries[0]["mode"] == "readonly"
    assert "timestamp" in entries[0]


def test_load_all_skips_malformed_lines(journal_path, tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(
        '{"signature": {"kind": "Pod"}, "timestamp": "t1"}\n'
        "not json at all\n"
        '{"signature": {"kind": "Node"}, "timestamp": "t2"}\n',
        encoding="utf-8",
    )
    entries = load_all()
    assert len(entries) == 2


def _entry(kind, namespace, name, reason, ts):
    return {
        "timestamp": ts,
        "signature": {
            "kind": kind,
            "namespace": namespace,
            "name": name,
            "reason": reason,
        },
        "resolution": f"resolved {reason}",
    }


def test_find_similar_returns_empty_for_empty_signature():
    entries = [_entry("Pod", "argocd", "x", "OOMKilled", "t1")]
    sig = Signature(kind=None, namespace=None, name=None, reason=None)
    assert find_similar(sig, entries) == []


def test_find_similar_exact_match():
    entries = [
        _entry("Pod", "argocd", "x", "OOMKilled", "2026-01-01"),
        _entry("Pod", "argocd", "x", "OOMKilled", "2026-02-01"),
        _entry("Pod", "longhorn", "y", "OOMKilled", "2026-03-01"),
    ]
    sig = Signature(kind="Pod", namespace="argocd", name="x", reason="OOMKilled")
    result = find_similar(sig, entries)
    # Both exact-match entries returned, most recent first
    assert len(result) == 2
    assert result[0]["timestamp"] == "2026-02-01"
    assert result[1]["timestamp"] == "2026-01-01"


def test_find_similar_falls_through_to_namespace_tier():
    entries = [
        _entry(
            "Pod", "argocd", "y", "OOMKilled", "2026-01-01"
        ),  # same ns + reason, different name
        _entry("Pod", "longhorn", "z", "OOMKilled", "2026-02-01"),  # different ns
    ]
    sig = Signature(kind="Pod", namespace="argocd", name="x", reason="OOMKilled")
    result = find_similar(sig, entries)
    # tier 1 (exact) = 0 hits; tier 2 (ns+reason) = 1 hit; tier 3 (reason) = 2 hits but we already have 1
    # Result should include the tier-2 entry first, then the tier-3 entry to fill
    assert len(result) == 2
    assert result[0]["signature"]["namespace"] == "argocd"


def test_find_similar_respects_limit():
    entries = [
        _entry("Pod", "argocd", "x", "OOMKilled", f"2026-01-0{i}") for i in range(1, 9)
    ]
    sig = Signature(kind="Pod", namespace="argocd", name="x", reason="OOMKilled")
    result = find_similar(sig, entries, limit=3)
    assert len(result) == 3


def test_format_for_prompt_empty_returns_empty_string():
    assert format_for_prompt([]) == ""


def test_format_for_prompt_renders_markdown_block():
    entries = [
        _entry("Pod", "argocd", "x", "OOMKilled", "2026-01-01T00:00:00Z"),
    ]
    text = format_for_prompt(entries)
    assert "Prior incidents" in text
    assert "OOMKilled" in text
    assert "argocd" in text
