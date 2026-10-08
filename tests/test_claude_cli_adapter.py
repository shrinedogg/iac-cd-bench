"""
Unit tests for ClaudeCliAdapter (bench/runner.py).

All subprocess calls are stubbed - nothing here shells out to a real `claude`
binary or makes a live call. See tools/run_matrix.sh / the #40 sign-off for
the one live smoke run this adapter is meant to enable.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bench.runner import ClaudeCliAdapter, ModelAdapter


def _fake_result(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "is_error": False,
        "type": "result",
        "subtype": "success",
        "result": "pong",
        "session_id": "fake-session",
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 9, "output_tokens": 48},
    }
    payload.update(overrides)
    return json.dumps(payload)


class _FakeCompletedProcess:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_is_model_adapter_subclass():
    assert issubclass(ClaudeCliAdapter, ModelAdapter)


def test_name_is_model():
    adapter = ClaudeCliAdapter("claude-haiku-4-5")
    assert adapter.name == "claude-haiku-4-5"


def test_build_command_pins_model_and_disables_tools_and_settings():
    adapter = ClaudeCliAdapter("claude-haiku-4-5")
    cmd = adapter._build_command()
    assert cmd[0] == "claude"
    assert "--print" in cmd
    assert "--output-format" in cmd and "json" in cmd
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-4-5"
    # One-shot completion only: no tool use, no CLAUDE.md/settings pickup.
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--setting-sources" in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == ""


def test_build_command_replaces_default_system_prompt():
    # #59: `--tools ""` alone did not stop the model from behaving like an
    # agent (narrating tool use it can't perform, or stalling out asking for
    # tool/file access) - the default Claude Code system prompt still frames
    # the session as an agentic coding tool. A full `--system-prompt`
    # override removed that framing in live probes; `--append-system-prompt`
    # (layered on top, not a replacement) did not get the same validation.
    adapter = ClaudeCliAdapter("claude-haiku-4-5")
    cmd = adapter._build_command()
    assert "--append-system-prompt" not in cmd
    assert "--system-prompt" in cmd
    prompt = cmd[cmd.index("--system-prompt") + 1]
    assert "no tools" in prompt
    assert "one-shot" in prompt or "single" in prompt.lower()


def test_build_command_pins_effort_when_given():
    adapter = ClaudeCliAdapter("claude-haiku-4-5", reasoning_effort="low")
    cmd = adapter._build_command()
    assert "--effort" in cmd
    assert cmd[cmd.index("--effort") + 1] == "low"


def test_build_command_omits_effort_when_none():
    adapter = ClaudeCliAdapter("claude-haiku-4-5", reasoning_effort=None)
    cmd = adapter._build_command()
    assert "--effort" not in cmd


def test_build_command_omits_effort_when_literal_none_string():
    adapter = ClaudeCliAdapter("claude-haiku-4-5", reasoning_effort="none")
    cmd = adapter._build_command()
    assert "--effort" not in cmd


def test_reasoning_effort_recorded_on_adapter_for_result_json():
    # bench.runner.run_task does getattr(adapter, "reasoning_effort", None)
    # to stamp every run JSON - this is how effort pinning becomes auditable.
    adapter = ClaudeCliAdapter("claude-haiku-4-5", reasoning_effort="low")
    assert adapter.reasoning_effort == "low"


def test_complete_parses_result_and_usage(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["timeout"] = timeout
        return _FakeCompletedProcess(_fake_result())

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = ClaudeCliAdapter("claude-haiku-4-5", reasoning_effort="low")
    out = adapter.complete("say pong", [])

    assert out["content"] == "pong"
    assert out["input_tokens"] == 9
    assert out["output_tokens"] == 48
    assert out["cost_usd"] == 0.01
    assert out["session_id"] == "fake-session"
    assert captured["input"] == "say pong"
    assert "--effort" in captured["cmd"]


def test_complete_appends_workspace_files_to_prompt(monkeypatch, tmp_path):
    captured: dict[str, Any] = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        captured["input"] = input
        return _FakeCompletedProcess(_fake_result())

    monkeypatch.setattr(subprocess, "run", fake_run)

    f = tmp_path / "main.tf"
    f.write_text("resource \"x\" {}\n")

    adapter = ClaudeCliAdapter("claude-haiku-4-5")
    adapter.complete("describe this", [f])

    assert "describe this" in captured["input"]
    assert "main.tf" in captured["input"]
    assert 'resource "x" {}' in captured["input"]


def test_complete_raises_on_nonzero_exit(monkeypatch):
    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        return _FakeCompletedProcess("", stderr="boom", returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = ClaudeCliAdapter("claude-haiku-4-5")
    with pytest.raises(RuntimeError, match="exited 1"):
        adapter.complete("hi", [])


def test_complete_raises_on_invalid_json(monkeypatch):
    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        return _FakeCompletedProcess("not json")

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = ClaudeCliAdapter("claude-haiku-4-5")
    with pytest.raises(RuntimeError, match="valid JSON"):
        adapter.complete("hi", [])


def test_complete_raises_when_cli_reports_error(monkeypatch):
    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        return _FakeCompletedProcess(_fake_result(is_error=True, result=""))

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = ClaudeCliAdapter("claude-haiku-4-5")
    with pytest.raises(RuntimeError, match="error result"):
        adapter.complete("hi", [])


def test_complete_raises_on_timeout(monkeypatch):
    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = ClaudeCliAdapter("claude-haiku-4-5", timeout=5)
    with pytest.raises(RuntimeError, match="timed out"):
        adapter.complete("hi", [])


def test_complete_raises_when_binary_missing(monkeypatch):
    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = ClaudeCliAdapter("claude-haiku-4-5", claude_bin="not-a-real-claude-binary")
    with pytest.raises(RuntimeError, match="not found on PATH"):
        adapter.complete("hi", [])


def test_model_provider_flag_accepts_claude_cli():
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "bench.runner", "--help"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert "claude-cli" in result.stdout


def test_build_judge_accepts_claude_cli_provider():
    """The judge's adapter-factory pathway (bench/judge.py build_judge) must
    also be constructible with ClaudeCliAdapter - required when no
    ANTHROPIC_API_KEY is set and both the model under test and the judge run
    against the machine's Claude Code auth."""
    from bench import judge as judge_mod

    judge = judge_mod.build_judge(model="claude-haiku-4-5", provider="claude-cli")
    assert isinstance(judge.adapter, ClaudeCliAdapter)
    assert judge.adapter.model == "claude-haiku-4-5"
    assert judge.adapter.reasoning_effort == "none"
