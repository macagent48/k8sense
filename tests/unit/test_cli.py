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


def test_doctor_check_reports_no_auth_when_both_missing(monkeypatch, tmp_path):
    # Both ANTHROPIC_API_KEY and claude CLI absent → failure
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # empty PATH hides claude CLI
    findings = doctor_check()
    assert any("no auth" in f.message and not f.ok for f in findings)


def test_doctor_check_accepts_claude_cli_oauth(monkeypatch, tmp_path):
    # API key absent, claude CLI present on PATH → success
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Create a fake claude executable on a PATH we control
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho 2.1.0\n")
    fake_claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    findings = doctor_check()
    assert any("OAuth" in f.message and f.ok for f in findings)


def test_doctor_check_accepts_api_key(monkeypatch):
    # API key present → success regardless of claude CLI
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    findings = doctor_check()
    assert any("API billing" in f.message and f.ok for f in findings)


def test_doctor_check_reports_kubectl_presence(monkeypatch):
    findings = doctor_check()
    # Either present or absent — both produce a finding
    assert any("kubectl" in f.message for f in findings)


def test_main_ask_handles_exception_with_one_line_error(monkeypatch, capsys):
    """If run_ask raises, main should print a one-line error and exit 1."""
    import k8sense.cli as cli_mod

    async def boom(question, renderer, model_id=None, mode=None):
        raise RuntimeError("simulated SDK failure")

    monkeypatch.setattr(cli_mod, "run_ask", boom)
    exit_code = cli_mod.main(["ask", "anything"])
    captured = capsys.readouterr()
    assert exit_code == 1
    # The error message should appear once, prefixed by the renderer's error glyph.
    assert "simulated SDK failure" in captured.out + captured.err
    # And it should NOT be a Python traceback.
    assert "Traceback" not in captured.out + captured.err


def test_parser_accepts_mcp_subcommand():
    parser = build_parser()
    ns = parser.parse_args(["mcp"])
    assert ns.command == "mcp"


def test_ask_accepts_propose_flag():
    parser = build_parser()
    ns = parser.parse_args(["ask", "--propose", "fix it"])
    assert ns.permission_mode_flag == "propose"
    assert ns.question == "fix it"


def test_ask_accepts_auto_fix_flag():
    parser = build_parser()
    ns = parser.parse_args(["ask", "--auto-fix", "fix it"])
    assert ns.permission_mode_flag == "auto-safe"


def test_ask_without_flag_has_no_mode_flag():
    parser = build_parser()
    ns = parser.parse_args(["ask", "just a question"])
    assert ns.permission_mode_flag is None


def test_doctor_finds_permission_mode_with_default(monkeypatch):
    monkeypatch.delenv("K8SENSE_PERMISSION_MODE", raising=False)
    findings = doctor_check()
    mode_findings = [f for f in findings if "permission_mode" in f.message]
    assert len(mode_findings) == 1
    assert "readonly" in mode_findings[0].message
    assert "default" in mode_findings[0].message


def test_doctor_reports_env_override(monkeypatch):
    monkeypatch.setenv("K8SENSE_PERMISSION_MODE", "auto-safe")
    findings = doctor_check()
    mode_findings = [f for f in findings if "permission_mode" in f.message]
    assert "auto-safe" in mode_findings[0].message
    assert "env" in mode_findings[0].message.lower()


def test_doctor_reports_invalid_env_with_fallthrough(monkeypatch):
    monkeypatch.setenv("K8SENSE_PERMISSION_MODE", "nonsense")
    findings = doctor_check()
    mode_findings = [f for f in findings if "permission_mode" in f.message]
    assert len(mode_findings) == 1
    assert not mode_findings[0].ok  # invalid
    assert "nonsense" in mode_findings[0].message
    assert (
        "falling through" in mode_findings[0].message
        or "fall through" in mode_findings[0].message
    )
