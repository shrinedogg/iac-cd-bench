"""
Semantic assertion stage runner for IaC/CD benchmark.

Runs pytest assertions against the model's workspace (rendered/planned
artifacts plus raw model output). Tests execute with cwd pinned to the
workspace so relative paths resolve against what the model produced,
not against the repo root.
"""

from __future__ import annotations

import re
import subprocess
import sys
import logging
from pathlib import Path

from bench.stages import lint

log = logging.getLogger(__name__)


def _read_threshold(task_dir: Path) -> float | None:
    """Read stages.semantic.pass_threshold from spec.yaml if present."""
    spec_path = task_dir / "spec.yaml"
    if not spec_path.exists():
        return None
    try:
        import yaml

        with open(spec_path) as f:
            spec = yaml.safe_load(f) or {}
        threshold = (
            spec.get("stages", {}).get("semantic", {}).get("pass_threshold")
        )
        return float(threshold) if threshold is not None else None
    except Exception:  # pragma: no cover - malformed spec falls back
        return None


def run_semantic(task_dir: Path, workspace: Path | None = None) -> dict:
    """Run pytest semantic assertions if tests/ exists.

    Args:
        task_dir: task definition dir (tests/, spec.yaml, golden/).
        workspace: the model's materialized workspace; used as pytest cwd so
            assertions see extracted files and model_output.md. Falls back to
            task_dir when omitted (legacy behavior).
    """
    test_file = task_dir / "tests" / "test_task.py"
    if not test_file.exists():
        # A task with no grader was not semantically checked. Recording that
        # as a pass handed every run of the 20 task dirs that ship no
        # tests/test_task.py a free third of its correctness axis. See
        # bench.stages.lint.inapplicable.
        return {
            **lint.inapplicable("no semantic tests", "gate_defect"),
            "passed_count": 0,
            "total_count": 0,
            "safety_pass": True,
        }

    cwd = workspace if workspace is not None else task_dir
    log.info("Running pytest: %s (cwd=%s)", test_file, cwd)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=line", "-p", "no:cacheprovider",
             str(test_file)],
            capture_output=True, text=True, timeout=120, cwd=str(cwd),
        )

        output = proc.stdout + proc.stderr

        # Prefer the summary line ("3 passed, 5 failed in 0.12s"); fall back
        # to counting verbose PASSED/FAILED markers.
        passed_count = 0
        total_count = 0
        m_pass = re.search(r"(\d+) passed", output)
        m_fail = re.search(r"(\d+) failed", output)
        m_err = re.search(r"(\d+) error", output)
        if m_pass or m_fail or m_err:
            passed_count = int(m_pass.group(1)) if m_pass else 0
            total_count = passed_count \
                + (int(m_fail.group(1)) if m_fail else 0) \
                + (int(m_err.group(1)) if m_err else 0)
        else:
            passed_count = output.count(" PASSED")
            total_count = passed_count + output.count(" FAILED")

        # Threshold-based pass (partial credit) when spec defines one;
        # otherwise all assertions must pass (pytest exit 0).
        threshold = _read_threshold(task_dir)
        if threshold is not None and total_count > 0:
            passed = (passed_count / total_count) >= threshold
        else:
            passed = proc.returncode == 0

        return {
            "passed": passed,
            "logs": output[-2000:],
            "passed_count": passed_count,
            "total_count": total_count,
            "pass_threshold": threshold,
            "safety_pass": "safety" not in output.lower() or "safety PASSED" in output,
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "logs": "TIMEOUT: pytest exceeded 120s",
            "passed_count": 0,
            "total_count": 0,
            "safety_pass": False,
        }
    except FileNotFoundError:
        return {
            "passed": False,
            "logs": "NOT FOUND: pytest",
            "passed_count": 0,
            "total_count": 0,
            "safety_pass": False,
        }
