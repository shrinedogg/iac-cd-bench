"""
Regression tests for the six result-integrity failures this harness has
actually shipped. Each one cost hours before it was noticed, and each test
below reproduces the shape of the failure and asserts a guard catches it.

  1. A missing tool binary scored as a stage pass (#56)
  2. spec.yaml `stages.*.enabled` ignored, so disabled stages booked passes (#56)
  3. Vacuous passes: a run producing no output passes lint ("nothing to lint")
     and static ("nothing to build"), so the most broken runs score highest
  4. A provider returning agent-transcript preambles instead of completions (#59)
  5. Cross-worktree toolchain drift making two result sets silently
     non-comparable
  6. No harness commit or prompt hash recorded, so a re-run after a code change
     is silently a different experiment

No network and no real binaries: every subprocess and PATH lookup is stubbed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bench import preflight, provenance, validate, validity
from bench.score import compute_score, stage_inapplicable
from bench.stages import lint, semantic, static

ROOT = Path(__file__).resolve().parent.parent


def _no_binary(*args, **kwargs):
    raise FileNotFoundError("no such file or directory")


def _ok_proc(*args, **kwargs):
    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""
    return _Proc()


# ══════════════════════════════════════════════════════════════════════════
# 1. Missing tool binary scored as a pass (#56)
# ══════════════════════════════════════════════════════════════════════════

def test_preflight_refuses_to_start_when_a_required_binary_is_missing(monkeypatch):
    """The systemic form of #56: rather than discovering mid-matrix that
    `pulumi` was never installed, refuse before the first token is spent."""
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    with pytest.raises(preflight.PreflightError) as exc:
        preflight.check(["pulumi-python"])

    assert "pulumi" in exc.value.report["missing"]
    assert exc.value.report["passed"] is False
    assert "refusing to start" in str(exc.value).lower()


def test_preflight_records_name_and_version_of_every_required_binary(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, **kwargs):
        class _Proc:
            returncode = 0
            stdout = f"{Path(cmd[0]).name} v1.2.3\nextra noise\n"
            stderr = ""
        return _Proc()

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    report = preflight.check(["terraform"])

    assert report["passed"] is True
    assert report["toolchain"]["terraform"] == {
        "present": True, "path": "/usr/bin/terraform", "version": "terraform v1.2.3",
    }


def test_preflight_override_marks_the_set_partial(monkeypatch):
    """The deliberate-partial-run escape hatch must not produce a clean set."""
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    report = preflight.check(["terraform"], allow_missing=True)
    assert report["partial"] is True
    assert report["passed"] is False

    prov = provenance.build_provenance(
        provider="anthropic", model="m", reasoning_effort=None,
        toolchain=report["toolchain"], partial=report["partial"],
    )
    classification = validate.classify_run({"stages": {}, "provenance": prov})
    assert any("partial_toolchain" in r for r in classification["partial_reasons"])


def test_preflight_covers_every_binary_the_lint_stage_invokes():
    """STACK_BINARIES and LINT_COMMANDS must not drift apart — a binary the
    lint stage shells out to but the preflight never probes is exactly the
    hole #56 came through."""
    for stack, commands in lint.LINT_COMMANDS.items():
        probed = set(preflight.STACK_BINARIES.get(stack, ()))
        for cmd, _args, _desc in commands:
            name = Path(cmd).name
            if name.startswith("python"):
                continue  # the repo's own .venv interpreter, not a stack tool
            assert name in probed, (
                f"lint stage for {stack} invokes `{name}` but "
                f"preflight.STACK_BINARIES[{stack!r}] does not probe it"
            )


def test_stage_reports_failure_not_pass_when_its_binary_is_absent(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _no_binary)
    result = lint.run_lint(ROOT, "terraform")
    assert result["passed"] is False
    assert "NOT FOUND" in result["logs"]


def test_validator_rejects_a_stored_pass_recorded_with_its_binary_absent():
    """427 historical runs carry exactly this shape: `static.passed = True`
    with a log body of `NOT FOUND: pulumi`. Fixed at run time; still on disk,
    so the validator has to catch it too."""
    run = {
        "model": "m", "stack": "pulumi-python", "task": "T2-generate",
        "content": "x" * 400 + "\n```python\nimport pulumi\n```\n",
        "stages": {
            "lint": {"passed": True, "logs": "ruff: exit=0"},
            "static": {"passed": True, "logs": "NOT FOUND: pulumi"},
            "semantic": {"passed": True, "passed_count": 3, "total_count": 3},
        },
    }
    classification = validate.classify_run(run, spec={})
    assert classification["verdict"] == "invalid"
    assert any("tool_missing_scored_as_pass" in r for r in classification["invalid_reasons"])
    assert any("pulumi" in r for r in classification["invalid_reasons"])


# ══════════════════════════════════════════════════════════════════════════
# 2. Disabled stages booking fake passes (#56)
# ══════════════════════════════════════════════════════════════════════════

def test_disabled_stage_is_excluded_from_correctness_not_credited():
    result = {
        "stages": {
            "lint": {"skipped": True, "reason": "disabled by spec"},
            "static": {"skipped": True, "reason": "disabled by spec"},
            "semantic": {"passed": False, "passed_count": 0, "total_count": 4},
        }
    }
    scores = compute_score(result)
    assert scores["correctness"] == 0.0
    assert scores["attempted_stages"] == 1


def test_a_run_with_every_stage_disabled_scores_no_correctness():
    result = {"stages": {n: {"skipped": True} for n in ("lint", "static", "semantic")}}
    assert compute_score(result)["correctness"] == 0
    classification = validate.classify_run({**result, "content": "x" * 500}, spec={})
    assert any("no_stage_ran" in r for r in classification["partial_reasons"])


# ══════════════════════════════════════════════════════════════════════════
# 3. Vacuous passes — the highest-value guard
# ══════════════════════════════════════════════════════════════════════════

def test_lint_with_nothing_to_lint_is_inapplicable_not_passed(tmp_path):
    for stack in ("knr-ops", "crossplane", "bare"):
        result = lint.run_lint(tmp_path, stack)
        assert result.get("inapplicable") is True, stack
        assert "passed" not in result, stack


def test_lint_with_no_typescript_is_inapplicable_not_passed(tmp_path):
    result = lint.run_lint(tmp_path, "pulumi-typescript")
    assert result.get("inapplicable") is True
    assert "passed" not in result


def test_static_with_nothing_to_build_is_inapplicable_not_passed(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _ok_proc)
    for stack in ("knr-ops", "crossplane", "bare"):
        result = static.run_static(tmp_path, stack)
        assert result.get("inapplicable") is True, stack
        assert "passed" not in result, stack


def test_static_that_did_act_still_reports_a_verdict(tmp_path, monkeypatch):
    """The guard must not swallow real runs: a workspace whose kustomization
    actually renders a resource is acted on, so the stage reports pass/fail
    as before. `resources: []` would render nothing -- indistinguishable
    from a no-op build under invariant 1 -- so this needs a real reference
    and a build stub that echoes back real evidence (a `kind:` line), not
    the bare `_ok_proc` other tests here use for a workspace with nothing
    to build at all."""
    import shutil

    (tmp_path / "configmap.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n")
    (tmp_path / "kustomization.yaml").write_text("resources:\n  - configmap.yaml\n")

    def _rendered_proc(*args, **kwargs):
        class _Proc:
            returncode = 0
            stdout = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n"
            stderr = ""
        return _Proc()

    def _fake_which(name):
        if name in ("kustomize", "flux"):
            return f"/fake/bin/{name}"
        return None

    monkeypatch.setattr(subprocess, "run", _rendered_proc)
    monkeypatch.setattr(shutil, "which", _fake_which)
    result = static.run_static(tmp_path, "knr-ops")
    assert result.get("inapplicable") is not True
    assert result["passed"] is True


def test_semantic_with_no_grader_is_inapplicable_not_passed(tmp_path):
    result = semantic.run_semantic(tmp_path, tmp_path)
    assert result.get("inapplicable") is True
    assert "passed" not in result


def test_the_most_broken_run_no_longer_outscores_a_working_one():
    """The exact inversion the vacuous pass produced: a run that emitted
    nothing scored 2 of 3 stages (lint and static had nothing to check) while
    a run that emitted real but flawed code scored 1 of 3."""
    emitted_nothing = {
        "stages": {
            "lint": lint.inapplicable("no YAML files in workspace"),
            "static": lint.inapplicable("nothing to build in workspace"),
            "semantic": {"passed": False, "passed_count": 0, "total_count": 5},
        }
    }
    emitted_flawed_code = {
        "stages": {
            "lint": {"passed": True, "logs": "yq: exit=0"},
            "static": {"passed": False, "logs": "kustomize build: exit=1"},
            "semantic": {"passed": False, "passed_count": 2, "total_count": 5},
        }
    }
    broken = compute_score(emitted_nothing)
    working = compute_score(emitted_flawed_code)

    assert broken["correctness"] == 0.0
    assert working["correctness"] == pytest.approx(1 / 3)
    assert working["composite"] > broken["composite"]


def test_completeness_is_not_a_free_full_mark_when_no_assertion_ran():
    """`total_count == 0` used to score completeness 1.0 — full marks on the
    second-heaviest axis for a run nothing had checked. The axis is now
    dropped from the composite instead."""
    scores = compute_score({
        "stages": {
            "lint": {"passed": False},
            "semantic": lint.inapplicable("no semantic tests") | {
                "passed_count": 0, "total_count": 0, "safety_pass": True,
            },
        }
    })
    assert "completeness" not in scores["applicable_axes"]
    assert scores["composite"] < 0.5


def test_run_is_invalid_when_every_enabled_stage_was_inapplicable():
    run = {
        "model": "m", "stack": "knr-ops", "task": "T2-generate",
        "content": "x" * 500 + "\n```yaml\napiVersion: v1\n```\n",
        "stages": {
            "lint": lint.inapplicable("no YAML files in workspace"),
            "static": lint.inapplicable("nothing to build in workspace"),
            "semantic": lint.inapplicable("no semantic tests"),
        },
    }
    classification = validate.classify_run(run, spec={})
    assert classification["verdict"] == "invalid"
    assert any("all_stages_inapplicable" in r for r in classification["invalid_reasons"])


def test_historical_vacuous_passes_are_recognised_from_their_log_bodies():
    """Result JSONs written before the guard carry `passed: True` with a
    'nothing to act on' log. Without retroactive recognition the fix would
    apply only to future runs and every published composite would keep
    quoting the inflated number."""
    for marker in ("no YAML files in workspace", "no TypeScript files in workspace",
                   "static validation passed", "no semantic tests"):
        assert stage_inapplicable({"passed": True, "logs": marker}), marker

    # A real run that merely mentions the phrase inside a longer log is not
    # demoted: the marker is matched against the whole log body, not as a
    # substring.
    assert not stage_inapplicable({
        "passed": True,
        "logs": "kubeconform: exit=0\nnote: no YAML files in workspace/sub\n",
    })


# ══════════════════════════════════════════════════════════════════════════
# 4. Agent-transcript preambles instead of completions (#59)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("content", [
    # Three narration lines of the same kind. Shaped after the real one this
    # sweep found: results/kimi-k3/knr-ops/warm/T4-debug_run0.json, which
    # narrated shell commands it could not run and still scored static and
    # semantic as passes because both had nothing to act on.
    "Let me start by exploring the repository structure.\n"
    "Let me look at the key configuration files.\n"
    "Let me examine the SOPS configuration and the overlays.\n"
    + "Then the overlay would be patched accordingly. " * 8,
    "<function_calls>\n<invoke name=\"Read\">\n</invoke>\n</function_calls>" + " padding " * 60,
    "⏺ Read(infra/s3/bucket.yaml)\n⏺ Now updating the retention policy." + " more text " * 40,
    "I'll use the Bash tool to inspect the workspace." + " and then continue " * 40,
])
def test_agent_transcripts_are_rejected_not_scored(content):
    verdict = validity.check_content(content, expects_artifacts=False)
    assert verdict["verdict"] == "invalid"
    assert any("agent_transcript" in r for r in verdict["reasons"])


def test_an_empty_completion_is_rejected():
    assert validity.check_content("")["verdict"] == "invalid"
    assert validity.check_content(None)["verdict"] == "invalid"


def test_a_short_completion_is_rejected():
    verdict = validity.check_content("Use a Bucket resource.")
    assert verdict["verdict"] == "invalid"
    assert any("content_too_short" in r for r in verdict["reasons"])


def test_prose_only_answer_is_rejected_when_the_task_expects_artifacts():
    """This is what makes #59 and #3 the same failure: a completion with no
    code block gives lint and static nothing to check, so they pass vacuously
    and the run's gate rate goes UP because the provider was broken."""
    prose = (
        "The retention policy should be attached to the bucket rather than the "
        "overlay, and the storage class stays as it is in the base. "
    ) * 6
    verdict = validity.check_content(prose, expects_artifacts=True)
    assert verdict["verdict"] == "invalid"
    assert any("no_extractable_output" in r for r in verdict["reasons"])

    # Same prose, on a task whose stages are all disabled, is a fine answer.
    assert validity.check_content(prose, expects_artifacts=False)["verdict"] == "valid"


def test_a_real_answer_passes_the_validity_gate():
    content = (
        "Three things in the brief conflict; I resolved them as follows. The "
        "bucket keeps its base storage class and the overlay only patches the "
        "retention policy, so the shared labels survive the merge.\n\n"
        "`infra/s3/logs/bucket.yaml`\n\n"
        "```yaml\napiVersion: s3.aws.upbound.io/v1beta1\nkind: Bucket\n"
        "metadata:\n  name: logs\n```\n"
    )
    verdict = validity.check_content(content, expects_artifacts=True)
    assert verdict["verdict"] == "valid", verdict["reasons"]


def test_expects_artifacts_follows_the_specs_enabled_stages():
    assert validity.expects_artifacts({"stages": {"lint": {"enabled": True}}}) is True
    assert validity.expects_artifacts({
        "stages": {n: {"enabled": False} for n in ("lint", "static", "semantic", "e2e")}
    }) is False
    # Default-True parity with runner._stage_enabled for specs without stages.
    assert validity.expects_artifacts({}) is True


def test_a_review_task_answer_with_one_narration_line_survives():
    """The narration threshold is two markers, not one: a review answer that
    opens 'Let me walk through what changed' must not be voided."""
    content = (
        "Let me walk through the three problems in this composition, worst "
        "first. The IAM role grants s3:* where the brief asks for read-only, "
        "which is the only one that is a security issue rather than a style "
        "one. The second is the missing region pin. The third is cosmetic. "
    ) * 3
    assert validity.check_content(content)["verdict"] == "valid"


# ══════════════════════════════════════════════════════════════════════════
# 5. Cross-worktree toolchain drift
# ══════════════════════════════════════════════════════════════════════════

def _set_report(label, *, commit="abc1234", tf="1.9.0", provider="anthropic",
                effort="high"):
    return {
        "label": label,
        "harness_commits": [commit],
        "toolchains": {"terraform": [f"Terraform v{tf}"]},
        "providers": [provider],
        "efforts": [effort],
    }


def test_comparison_refuses_when_toolchain_versions_differ():
    comp = validate.comparability([
        _set_report("set-a", tf="1.9.0"),
        _set_report("set-b", tf="1.7.2"),
    ])
    assert comp["comparable"] is False
    assert any("toolchain differs" in c for c in comp["conflicts"])


def test_comparison_refuses_when_harness_commits_differ():
    comp = validate.comparability([
        _set_report("set-a", commit="abc1234"),
        _set_report("set-b", commit="def5678"),
    ])
    assert comp["comparable"] is False
    assert any("harness commit differs" in c for c in comp["conflicts"])


def test_comparison_refuses_when_provider_or_effort_differ():
    assert not validate.comparability([
        _set_report("a", provider="anthropic"), _set_report("b", provider="claude-cli"),
    ])["comparable"]
    assert not validate.comparability([
        _set_report("a", effort="high"), _set_report("b", effort="low"),
    ])["comparable"]


def test_identical_provenance_compares_cleanly():
    comp = validate.comparability([_set_report("a"), _set_report("b")])
    assert comp["comparable"] is True
    assert comp["unverifiable"] == []


def test_missing_provenance_is_unverifiable_not_agreement():
    """A set that cannot be shown to differ has not been shown to match."""
    bare = {"label": "old-set", "harness_commits": [], "toolchains": {},
            "providers": [], "efforts": ["high"]}
    comp = validate.comparability([_set_report("new-set"), bare])
    assert comp["unverifiable"]
    assert any("old-set" in u for u in comp["unverifiable"])


def test_toolchain_fingerprint_ignores_paths_but_tracks_versions():
    """Two worktrees resolve the same binary at different paths without any
    behavioural difference; a fingerprint that moved on path alone would flag
    every set as incomparable and be ignored within a week."""
    a = {"tsc": {"present": True, "path": "/opt/a/tsc", "version": "5.4.2"}}
    b = {"tsc": {"present": True, "path": "/opt/b/tsc", "version": "5.4.2"}}
    c = {"tsc": {"present": True, "path": "/opt/a/tsc", "version": "5.6.0"}}
    assert provenance.toolchain_fingerprint(a) == provenance.toolchain_fingerprint(b)
    assert provenance.toolchain_fingerprint(a) != provenance.toolchain_fingerprint(c)


def test_report_compare_cli_refuses_incomparable_sets(tmp_path):
    """End to end: two sets whose provenance conflicts must not render a
    side-by-side table without --allow-incomparable."""
    import sys

    content = "x" * 400 + "\n```yaml\napiVersion: v1\nkind: ConfigMap\n```\n"

    def write(dir_: Path, commit: str, tf: str) -> None:
        out = dir_ / "terraform" / "warm"
        out.mkdir(parents=True, exist_ok=True)
        toolchain = {"terraform": {"present": True, "path": "/usr/bin/terraform",
                                   "version": f"Terraform v{tf}"}}
        (out / "T2-generate_run0.json").write_text(json.dumps({
            "model": dir_.name, "task": "T2-generate", "stack": "terraform",
            "run": 0, "condition": "warm", "content": content,
            "provenance": provenance.build_provenance(
                provider="anthropic", model=dir_.name, reasoning_effort="high",
                toolchain=toolchain,
                extra={"harness": {"commit": commit, "dirty": False, "branch": "main"},
                       "task": {"prompt_sha256": "deadbeef", "spec_sha256": "cafe"}},
            ),
            "stages": {
                "lint": {"passed": True, "logs": "terraform validate: exit=0"},
                "static": {"passed": True, "logs": "terraform validate: exit=0"},
                "semantic": {"passed": True, "passed_count": 2, "total_count": 2,
                             "safety_pass": True},
            },
        }))

    a, b = tmp_path / "model-a", tmp_path / "model-b"
    write(a, "aaaaaaa", "1.9.0")
    write(b, "bbbbbbb", "1.7.2")

    proc = subprocess.run(
        [sys.executable, "-m", "bench.report", "--compare", str(a), str(b),
         "--output", str(tmp_path / "out.md")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 2, proc.stdout
    assert "not the same experiment" in proc.stderr
    assert not (tmp_path / "out.md").exists()

    proc = subprocess.run(
        [sys.executable, "-m", "bench.report", "--compare", str(a), str(b),
         "--allow-incomparable", "--output", str(tmp_path / "out.md")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    rendered = (tmp_path / "out.md").read_text()
    assert "CONFLICT" in rendered


# ══════════════════════════════════════════════════════════════════════════
# 6. No harness commit or prompt hash recorded
# ══════════════════════════════════════════════════════════════════════════

def test_provenance_records_harness_commit_and_toolchain():
    prov = provenance.build_provenance(
        provider="anthropic", model="claude-opus-5", reasoning_effort="max",
        toolchain={"yq": {"present": True, "path": "/usr/bin/yq", "version": "4.44"}},
    )
    assert prov["provider"] == "anthropic"
    assert prov["model"] == "claude-opus-5"
    assert prov["reasoning_effort"] == "max"
    assert "commit" in prov["harness"] and "dirty" in prov["harness"]
    assert prov["toolchain"]["yq"]["version"] == "4.44"


def test_task_fingerprint_moves_when_the_prompt_changes(tmp_path):
    task = tmp_path / "T2-generate"
    task.mkdir()
    (task / "prompt.md").write_text("Write the bucket manifest.\n")
    (task / "spec.yaml").write_text("stack: knr-ops\n")
    before = provenance.task_fingerprint(task)

    (task / "prompt.md").write_text("Write the bucket manifest, with retention.\n")
    after = provenance.task_fingerprint(task)

    assert before["prompt_sha256"] != after["prompt_sha256"]
    assert before["spec_sha256"] == after["spec_sha256"]


def test_a_run_without_provenance_is_partial_and_uncomparable():
    run = {
        "model": "m", "stack": "terraform", "task": "T2-generate",
        "content": "x" * 400 + "\n```hcl\nresource \"aws_s3_bucket\" \"a\" {}\n```\n",
        "stages": {"lint": {"passed": True, "logs": "terraform validate: exit=0"},
                   "semantic": {"passed": True, "passed_count": 1, "total_count": 1}},
    }
    classification = validate.classify_run(run, spec={})
    assert classification["verdict"] == "partial"
    assert any("no_provenance" in r for r in classification["partial_reasons"])


def test_run_task_stamps_provenance_and_a_validity_verdict():
    """End to end through the runner with a stub adapter: every result must
    carry the provenance block and the gate's verdict, so nothing downstream
    has to guess what produced it."""
    from bench import runner

    class StubModel:
        name = "stub-model"
        reasoning_effort = "high"

        def complete(self, prompt, files):
            return {"content": "short", "input_tokens": 1, "output_tokens": 2}

    task_dir = ROOT / "tasks" / "knr-ops" / "T2-generate"
    results = runner.run_task(task_dir, StubModel(), k=1)
    result = results[0]

    prov = result["provenance"]
    assert prov["model"] == "stub-model"
    assert prov["reasoning_effort"] == "high"
    assert prov["task"]["prompt_sha256"]
    assert prov["task"]["spec_sha256"]
    assert "commit" in prov["harness"]
    assert prov["condition"] == "warm" and prov["k"] == 1

    # A five-character completion is not an answer.
    assert result["validity"]["verdict"] == "invalid"
    assert any("content_too_short" in r for r in result["validity"]["reasons"])


def test_a_dirty_harness_tree_is_flagged():
    """A run produced from a tree with uncommitted harness edits is not
    reproducible from the recorded sha, so the sha alone cannot carry the
    comparability claim."""
    prov = provenance.build_provenance(
        provider="anthropic", model="m", reasoning_effort=None,
        toolchain={"yq": {"present": True, "path": "/usr/bin/yq", "version": "4"}},
        extra={"harness": {"commit": "abc1234", "dirty": True, "branch": "main"},
               "task": {"prompt_sha256": "abc", "spec_sha256": "def"}},
    )
    run = {
        "model": "m", "stack": "terraform", "task": "T2-generate",
        "content": "x" * 400 + "\n```hcl\nresource \"aws_s3_bucket\" \"a\" {}\n```\n",
        "provenance": prov,
        "stages": {"lint": {"passed": True, "logs": "ok"},
                   "semantic": {"passed": True, "passed_count": 1, "total_count": 1}},
    }
    classification = validate.classify_run(run, spec={})
    assert any("dirty_harness" in r for r in classification["partial_reasons"])


# ══════════════════════════════════════════════════════════════════════════
# Set-level validation
# ══════════════════════════════════════════════════════════════════════════

def _write_run(dir_: Path, task: str, run: int, *, stack: str = "terraform",
               **overrides) -> None:
    out = dir_ / stack / "warm"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": dir_.name, "task": task, "stack": stack, "run": run,
        "condition": "warm",
        "content": "x" * 400 + "\n```hcl\nresource \"aws_s3_bucket\" \"a\" {}\n```\n",
        "stages": {
            "lint": {"passed": True, "logs": "terraform validate: exit=0"},
            "static": {"passed": True, "logs": "terraform validate: exit=0"},
            "semantic": {"passed": True, "passed_count": 2, "total_count": 2,
                         "safety_pass": True},
        },
    }
    payload.update(overrides)
    (out / f"{task}_run{run}.json").write_text(json.dumps(payload))


def _crash_overrides(error: str) -> dict:
    """Mirrors bench.runner's exception handler (runner.py, the `except
    Exception as e` block around line 781): one harness failure stamps
    `result["error"]` AND `result["validity"]["reasons"]` with the same
    `runner_error` text, at the same call site."""
    return {
        "error": error,
        "validity": {
            "valid": False, "reason": "runner_error", "content_length": None,
            "verdict": "invalid", "reasons": [f"runner_error: {error}"],
        },
    }


def test_uneven_k_is_refused_as_not_the_same_experiment(tmp_path):
    """chant-bench's trial-count check: a 1-run task beside 3-run tasks is a
    smoke test that landed on the leaderboard."""
    d = tmp_path / "set"
    for r in range(3):
        _write_run(d, "T2-generate", r)
    _write_run(d, "T3-modify", 0)

    report = validate.validate_result_set(d)
    assert report["verdict"] == "refused"
    assert any("uneven k" in p for p in report["problems"])


def test_errored_runs_stay_in_the_denominator_and_refuse_above_the_limit(tmp_path):
    """A run that died in the harness is a run that was asked for and never
    answered. Dropping it from both sides of the ratio is how a catastrophe
    reads as a triumph."""
    d = tmp_path / "set"
    for r in range(9):
        _write_run(d, "T2-generate", r)
    _write_run(d, "T2-generate", 9, error="HTTP 500 after 10 retries")
    _write_run(d, "T2-generate", 10, error="HTTP 500 after 10 retries")

    report = validate.validate_result_set(d)
    assert report["total"] == 11
    assert report["errored"] == 2
    assert report["error_share"] > validate.CRASH_LIMIT
    assert any("died in the harness" in p for p in report["problems"])
    assert report["verdict"] == "refused"


def test_runner_error_is_not_double_counted_from_the_stored_verdict():
    """classify_run's own `result.get('error')` check and the run's stored
    `validity` block (stamped by the same runner.py exception handler) both
    carry a `runner_error` reason for one harness failure. One failure, one
    reason."""
    result = {
        "model": "m", "stack": "terraform", "task": "T2-generate", "run": 0,
        "stages": {},
        **_crash_overrides("HTTP 500 after 10 retries"),
    }
    classification = validate.classify_run(result, spec={})
    runner_errors = [r for r in classification["invalid_reasons"] if r.startswith("runner_error")]
    assert len(runner_errors) == 1


def test_runner_error_dedup_keeps_the_more_informative_message():
    """Step 1 truncates `result['error']` to 160 chars; the stored verdict
    (step 2) carries the untruncated text from the same exception. When the
    two copies differ only in how much detail survived, keep the fuller one
    rather than whichever happened to be appended first."""
    long_error = "HTTP 500: " + "x" * 250
    result = {
        "model": "m", "stack": "terraform", "task": "T2-generate", "run": 0,
        "stages": {},
        **_crash_overrides(long_error),
    }
    classification = validate.classify_run(result, spec={})
    runner_errors = [r for r in classification["invalid_reasons"] if r.startswith("runner_error")]
    assert len(runner_errors) == 1
    assert runner_errors[0] == f"runner_error: {long_error}"


def test_crash_rate_at_the_limit_is_not_refused_after_dedup(tmp_path):
    """The concrete #40 numbers: 3 genuinely failed runs out of 36 is 8.3%,
    under CRASH_LIMIT. Before the dedup fix, each crashed run's runner_error
    reason was counted twice (once from `result['error']`, once from the
    run's own stored `validity`), so this same set read as 6/36 = 17% and
    was refused."""
    d = tmp_path / "set"
    for r in range(33):
        _write_run(d, "T2-generate", r)
    for r in range(33, 36):
        _write_run(d, "T2-generate", r, **_crash_overrides("HTTP 500 after 10 retries"))

    report = validate.validate_result_set(d)
    assert report["total"] == 36
    assert report["errored"] == 3
    assert report["error_share"] == pytest.approx(3 / 36)
    assert report["error_share"] <= validate.CRASH_LIMIT
    assert not any("died in the harness" in p for p in report["problems"])
    assert report["verdict"] != "refused"


def test_crash_rate_above_the_limit_is_still_refused(tmp_path):
    """4 of 36 is 11.1%, over CRASH_LIMIT even after the double-count is
    fixed - the gate must still catch a genuine crash rate."""
    d = tmp_path / "set"
    for r in range(32):
        _write_run(d, "T2-generate", r)
    for r in range(32, 36):
        _write_run(d, "T2-generate", r, **_crash_overrides("HTTP 500 after 10 retries"))

    report = validate.validate_result_set(d)
    assert report["total"] == 36
    assert report["errored"] == 4
    assert report["error_share"] > validate.CRASH_LIMIT
    assert any("died in the harness" in p for p in report["problems"])
    assert report["verdict"] == "refused"


# ══════════════════════════════════════════════════════════════════════════
# Toolchain drift: per binary, not per stack's whole probe
# ══════════════════════════════════════════════════════════════════════════

def _toolchain_prov(toolchain: dict) -> dict:
    return provenance.build_provenance(
        provider="anthropic", model="m", reasoning_effort="high", toolchain=toolchain,
        extra={"harness": {"commit": "abc1234", "dirty": False, "branch": "main"},
               "task": {"prompt_sha256": "abc", "spec_sha256": "def"}},
    )


def test_toolchain_check_passes_when_stacks_probe_different_binaries(tmp_path):
    """preflight only probes the binaries a stack actually needs, so a
    multi-stack set legitimately records different binary sets per run -
    verified from the actual #40 data: bare/chant/knr-ops each probe a
    different toolchain, but every binary two of them share (kubeconform,
    yq) agrees on version. That must not be an incomparable-toolchain
    refusal."""
    d = tmp_path / "set"
    bare_tc = {"kubeconform": {"version": "v0.7.0"},
               "kubectl": {"version": "Client Version: v1.36.1"},
               "yq": {"version": "v4.53.6"}}
    chant_tc = {"chant": {"version": "@intentius/chant 0.46.0"},
                "kubeconform": {"version": "v0.7.0"},
                "npm": {"version": "11.8.0"},
                "tsc": {"version": "Version 5.9.3"}}
    knr_tc = {"flux": {"version": "flux version 2.5.0"},
              "kubeconform": {"version": "v0.7.0"},
              "kustomize": {"version": "v5.6.0"},
              "yq": {"version": "v4.53.6"}}

    for stack, tc in (("bare", bare_tc), ("chant", chant_tc), ("knr-ops", knr_tc)):
        for r in range(3):
            _write_run(d, "T2-generate", r, stack=stack, provenance=_toolchain_prov(tc))

    report = validate.validate_result_set(d)
    assert not any("mixed toolchain" in p for p in report["problems"]), report["problems"]
    assert report["toolchains"]["kubeconform"] == ["v0.7.0"]
    assert report["toolchains"]["yq"] == ["v4.53.6"]


def test_toolchain_check_refuses_when_one_binary_genuinely_differs(tmp_path):
    """The check must still catch the failure mode it was written for: the
    SAME binary recorded at different versions within one set."""
    d = tmp_path / "set"
    tc_a = {"kubeconform": {"version": "v0.7.0"}, "yq": {"version": "v4.53.6"}}
    tc_b = {"kubeconform": {"version": "v0.8.1"}, "yq": {"version": "v4.53.6"}}

    _write_run(d, "T2-generate", 0, stack="bare", provenance=_toolchain_prov(tc_a))
    _write_run(d, "T2-generate", 1, stack="bare", provenance=_toolchain_prov(tc_b))

    report = validate.validate_result_set(d)
    assert report["verdict"] == "refused"
    conflict = next(p for p in report["problems"] if "mixed toolchain" in p)
    assert "kubeconform" in conflict
    assert "v0.7.0" in conflict and "v0.8.1" in conflict


def test_comparison_passes_when_sets_probe_different_binaries_that_agree():
    """Cross-set version of the same fix: two sets built from different
    stack mixes must not be flagged incomparable just because they recorded
    different binary sets, as long as every binary both sets share agrees."""
    set_a = {
        "label": "set-a", "harness_commits": ["abc1234"],
        "toolchains": {"kubeconform": ["v0.7.0"], "yq": ["v4.53.6"],
                       "kubectl": ["Client Version: v1.36.1"]},
        "providers": ["anthropic"], "efforts": ["high"],
    }
    set_b = {
        "label": "set-b", "harness_commits": ["abc1234"],
        "toolchains": {"kubeconform": ["v0.7.0"], "yq": ["v4.53.6"],
                       "flux": ["flux version 2.5.0"]},
        "providers": ["anthropic"], "efforts": ["high"],
    }
    comp = validate.comparability([set_a, set_b])
    assert comp["comparable"] is True, comp["conflicts"]


def test_comparison_refuses_when_one_binary_differs_across_sets():
    set_a = {
        "label": "set-a", "harness_commits": ["abc1234"],
        "toolchains": {"kubeconform": ["v0.7.0"]},
        "providers": ["anthropic"], "efforts": ["high"],
    }
    set_b = {
        "label": "set-b", "harness_commits": ["abc1234"],
        "toolchains": {"kubeconform": ["v0.8.1"]},
        "providers": ["anthropic"], "efforts": ["high"],
    }
    comp = validate.comparability([set_a, set_b])
    assert comp["comparable"] is False
    conflict = next(c for c in comp["conflicts"] if "toolchain" in c)
    assert "kubeconform" in conflict
    assert "v0.7.0" in conflict and "v0.8.1" in conflict


def test_a_clean_set_validates(tmp_path):
    d = tmp_path / "set"
    toolchain = {"terraform": {"present": True, "path": "/usr/bin/terraform",
                               "version": "Terraform v1.9.0"}}
    prov = provenance.build_provenance(
        provider="anthropic", model="m", reasoning_effort="high", toolchain=toolchain,
        extra={"harness": {"commit": "abc1234", "dirty": False, "branch": "main"},
               "task": {"prompt_sha256": "abc", "spec_sha256": "def"}},
    )
    for r in range(3):
        _write_run(d, "T2-generate", r, provenance=prov)

    report = validate.validate_result_set(d)
    assert report["verdict"] == "valid", report["problems"]
    assert report["rejected"] == 0
    assert report["counts"]["valid"] == 3


def test_validator_cli_exits_nonzero_on_a_refused_set(tmp_path):
    import sys

    d = tmp_path / "set"
    _write_run(d, "T2-generate", 0, content="")

    proc = subprocess.run(
        [sys.executable, "-m", "bench.validate", str(d)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 1
    assert "rejected: 1" in proc.stdout
    assert "REFUSED" in proc.stdout


# ══════════════════════════════════════════════════════════════════════════
# Reporting: a rejected run gets no number
# ══════════════════════════════════════════════════════════════════════════

def test_report_excludes_rejected_runs_and_states_the_count(tmp_path):
    from bench.report import generate_report, partition_by_validity
    from bench.score import compute_score as _score

    good = {
        "model": "m", "stack": "terraform", "task": "T2-generate", "run": 0,
        "condition": "warm",
        "content": "x" * 400 + "\n```hcl\nresource \"aws_s3_bucket\" \"a\" {}\n```\n",
        "stages": {"lint": {"passed": True, "logs": "ok"},
                   "static": {"passed": True, "logs": "ok"},
                   "semantic": {"passed": True, "passed_count": 2, "total_count": 2,
                                "safety_pass": True}},
    }
    bad = {**good, "run": 1, "content": ""}
    for r in (good, bad):
        r["score"] = _score(r)

    scored, rejected = partition_by_validity([dict(good), dict(bad)])
    assert len(scored) == 1 and len(rejected) == 1

    report = generate_report("m", [dict(good), dict(bad)])
    assert "**rejected: 1**" in report
    assert "empty_completion" in report
    assert "Runs scored: 1 (rejected: 1)" in report


# ── reasoning effort must be uniform within a set ────────────────────────

def _prov(effort: str) -> dict:
    return {
        "provenance": {
            "harness": {"commit": "abc1234", "dirty": False},
            "provider": "claude-cli",
            "reasoning_effort": effort,
            "toolchain": {"terraform": {"version": "1.9.0"}},
        }
    }


def test_mixed_reasoning_effort_within_one_set_is_refused(tmp_path):
    """Effort was guarded across sets but not within one, so a set could mix
    it silently and still validate.

    The benchmark's author reports that reasoning effort moved results more
    than model choice did in places. That makes a set mixing efforts not one
    experiment but several averaged together — a harder refusal than the model
    axis, not a softer one.
    """
    d = tmp_path / "claude-haiku-4-5-mixed"
    _write_run(d, "T2-generate", 0, **_prov("low"))
    _write_run(d, "T3-modify", 0, **_prov("high"))

    report = validate.validate_result_set(d)

    assert report["verdict"] == "refused", report
    problem = "\n".join(report["problems"])
    assert "mixed reasoning effort" in problem
    assert "high" in problem and "low" in problem


def test_uniform_reasoning_effort_is_not_refused(tmp_path):
    """The other direction — the check must not fire on a correct set."""
    d = tmp_path / "claude-haiku-4-5-uniform"
    _write_run(d, "T2-generate", 0, **_prov("low"))
    _write_run(d, "T3-modify", 0, **_prov("low"))

    report = validate.validate_result_set(d)

    assert not any("reasoning effort" in p for p in report["problems"]), report["problems"]
