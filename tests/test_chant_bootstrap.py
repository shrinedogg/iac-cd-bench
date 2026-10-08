"""
Tests for the chant workspace node_modules/tsconfig bootstrap (issue #58):

1. bench.stages.e2e.ensure_chant_node_modules installs golden-base/chant's
   node_modules exactly once (idempotent on both @intentius packages being
   present), and is what backs both preflight_chant_golden and
   bench.runner's per-workspace bootstrap.
2. bench.runner._bootstrap_chant_workspace symlinks that shared
   node_modules into a materialized workspace and copies tsconfig.json +
   package.json from the same template, without clobbering seed content.
3. materialize_task only bootstraps chant workspaces whose spec actually
   runs a toolchain stage (lint/static/e2e) -- pure rubric/prediction chant
   tasks never see a node_modules tree.
4. run_task's workspace-files discovery excludes node_modules, so a
   materialized chant workspace's (symlinked) node_modules never gets read
   into the model prompt.

All subprocess calls are stubbed -- no real npm install, no network, no
dependency on chant/tsc being on PATH.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bench import runner
from bench.stages import e2e

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"


# ── ensure_chant_node_modules ───────────────────────────────────────────

def test_ensure_chant_node_modules_skips_install_when_already_present(tmp_path, monkeypatch):
    golden = tmp_path / "chant"
    (golden / "node_modules" / "@intentius" / "chant").mkdir(parents=True)
    (golden / "node_modules" / "@intentius" / "chant-lexicon-k8s").mkdir(parents=True)

    calls: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))

    result = e2e.ensure_chant_node_modules(golden)

    assert result == golden
    assert calls == [], "already-installed template must not re-run npm install"


def test_ensure_chant_node_modules_installs_when_missing(tmp_path, monkeypatch):
    golden = tmp_path / "chant"
    golden.mkdir()
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        (golden / "node_modules" / "@intentius" / "chant").mkdir(parents=True)
        (golden / "node_modules" / "@intentius" / "chant-lexicon-k8s").mkdir(parents=True)

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = e2e.ensure_chant_node_modules(golden)

    assert result == golden
    assert seen["cmd"][0] == "npm"
    assert "install" in seen["cmd"]
    assert seen["cwd"] == str(golden)


def test_ensure_chant_node_modules_reinstalls_partial_install(tmp_path, monkeypatch):
    """Only one of the two vendored packages present (a previous install
    interrupted mid-way) must not be treated as a complete, cached template."""
    golden = tmp_path / "chant"
    (golden / "node_modules" / "@intentius" / "chant").mkdir(parents=True)
    # chant-lexicon-k8s deliberately missing.

    calls: list[object] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        (golden / "node_modules" / "@intentius" / "chant-lexicon-k8s").mkdir(parents=True)

        class _Proc:
            returncode = 0

        return _Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    e2e.ensure_chant_node_modules(golden)
    assert len(calls) == 1


def test_preflight_reports_failed_install_without_crashing(tmp_path, monkeypatch):
    """A failed bootstrap (e.g. npm unavailable) must surface as a failed
    preflight dict, not an uncaught exception that kills the runner."""
    golden_root = tmp_path / "golden-base" / "chant"
    golden_root.mkdir(parents=True)
    monkeypatch.setattr(e2e, "ROOT", tmp_path)

    def boom(golden_dir=None):
        raise RuntimeError("npm install failed: network unreachable")

    monkeypatch.setattr(e2e, "ensure_chant_node_modules", boom)

    result = e2e.preflight_chant_golden()

    assert result["passed"] is False
    assert result["skipped"] is False
    assert "npm install failed" in result["logs"]


# ── bench.runner._bootstrap_chant_workspace ─────────────────────────────

def _fake_golden_template(tmp_path: Path) -> Path:
    golden = tmp_path / "golden"
    (golden / "node_modules").mkdir(parents=True)
    (golden / "tsconfig.json").write_text('{"compilerOptions": {"moduleResolution": "NodeNext"}}')
    (golden / "package.json").write_text('{"type": "module"}')
    return golden


def test_bootstrap_chant_workspace_symlinks_and_copies(tmp_path, monkeypatch):
    golden = _fake_golden_template(tmp_path)
    monkeypatch.setattr(runner.e2e, "ensure_chant_node_modules", lambda: golden)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    runner._bootstrap_chant_workspace(workspace)

    nm = workspace / "node_modules"
    assert nm.is_symlink()
    assert nm.resolve() == (golden / "node_modules").resolve()
    assert (workspace / "tsconfig.json").read_text() == (golden / "tsconfig.json").read_text()
    assert (workspace / "package.json").read_text() == (golden / "package.json").read_text()


def test_bootstrap_chant_workspace_does_not_clobber_seed_files(tmp_path, monkeypatch):
    golden = _fake_golden_template(tmp_path)
    monkeypatch.setattr(runner.e2e, "ensure_chant_node_modules", lambda: golden)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "tsconfig.json").write_text("seed-shipped-tsconfig")
    runner._bootstrap_chant_workspace(workspace)

    assert (workspace / "tsconfig.json").read_text() == "seed-shipped-tsconfig"
    assert (workspace / "package.json").read_text() == '{"type": "module"}'


def test_bootstrap_chant_workspace_removal_does_not_touch_template(tmp_path, monkeypatch):
    """shutil.rmtree on the ephemeral workspace (run_task's per-run cleanup)
    must remove the symlink itself, never recurse into and delete the
    shared template it points at."""
    import shutil

    golden = _fake_golden_template(tmp_path)
    (golden / "node_modules" / "marker.txt").write_text("still here")
    monkeypatch.setattr(runner.e2e, "ensure_chant_node_modules", lambda: golden)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    runner._bootstrap_chant_workspace(workspace)

    shutil.rmtree(workspace, ignore_errors=True)

    assert (golden / "node_modules" / "marker.txt").exists()


# ── materialize_task gating ─────────────────────────────────────────────

def test_materialize_task_bootstraps_chant_when_lint_enabled(tmp_path, monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(runner, "_bootstrap_chant_workspace", lambda ws: calls.append(ws))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    runner.materialize_task(TASKS_DIR / "chant" / "T2-generate", workspace, condition="warm")

    assert calls == [workspace]


def test_materialize_task_skips_bootstrap_for_pure_rubric_chant_task(tmp_path, monkeypatch):
    """T1-comprehend disables lint/static/semantic/e2e entirely -- its
    workspace and grader should never see a node_modules tree."""
    calls: list[Path] = []
    monkeypatch.setattr(runner, "_bootstrap_chant_workspace", lambda ws: calls.append(ws))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    runner.materialize_task(TASKS_DIR / "chant" / "T1-comprehend", workspace, condition="warm")

    assert calls == []


def test_materialize_task_skips_bootstrap_for_non_chant_stack(tmp_path, monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(runner, "_bootstrap_chant_workspace", lambda ws: calls.append(ws))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    runner.materialize_task(TASKS_DIR / "knr-ops" / "T2-generate", workspace, condition="warm")

    assert calls == []


# ── run_task excludes node_modules from the model-visible file list ────

def test_run_task_excludes_node_modules_from_workspace_files(monkeypatch):
    captured: dict[str, list[Path]] = {}

    class StubModel:
        name = "stub-model"

        def complete(self, prompt, files):
            captured["files"] = list(files)
            return {"content": "no code blocks here", "input_tokens": 1, "output_tokens": 1}

    def fake_bootstrap(workspace: Path) -> None:
        nm_pkg = workspace / "node_modules" / "@intentius" / "chant"
        nm_pkg.mkdir(parents=True)
        (nm_pkg / "index.d.ts").write_text("declare const x: number;\n")

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(runner, "_bootstrap_chant_workspace", fake_bootstrap)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())

    runner.run_task(TASKS_DIR / "chant" / "T2-generate", StubModel(), k=1)

    files = captured["files"]
    assert files, "expected some workspace files to be discovered"
    assert not any("node_modules" in p.parts for p in files), (
        "node_modules must never be sent to the model as a workspace file"
    )
