"""CLI argument parsing and dispatch."""

import pytest

from k8sense.cli import build_parser, doctor_check


def test_parser_accepts_ask_subcommand():
    parser = build_parser()
    ns = parser.parse_args(["ask", "why is pod X crashing?"])
    assert ns.command == "ask"
    assert ns.question == "why is pod X crashing?"


def test_parser_accepts_doctor_subcommand():
    parser = build_parser()
    ns = parser.parse_args(["doctor"])
    assert ns.command == "doctor"


def test_parser_rejects_unknown_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["frobnicate"])


def test_doctor_check_reports_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    findings = doctor_check()
    assert any("ANTHROPIC_API_KEY" in f.message and not f.ok for f in findings)


def test_doctor_check_reports_kubectl_presence(monkeypatch):
    findings = doctor_check()
    # Either present or absent — both produce a finding
    assert any("kubectl" in f.message for f in findings)
