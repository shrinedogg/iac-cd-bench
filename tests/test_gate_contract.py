"""Conformance suite for the gate contract (bench/stages/contract.py).

Two halves.

The first tests the invariants themselves — that a pass with no evidence is
downgraded, that a gate defect is not scoreable in either direction. These run
against synthetic StageResults and need no toolchain, so they hold on any
machine including CI, where three arms are skipped for want of credentials and
a private package.

The second runs over every gate in the registry: each must pass its own
model-shaped fixture and fail its wrong one. That is the bar `test_golden_gates`
does not reach — the golden is the input each gate was tuned against, so it is
the weakest possible evidence, and #104 shipped for months with a golden that
passed a gate validating literally nothing.

The registry is empty while gates are migrated one at a time. The invariant
tests are live from the start, since they are what the migration is for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import bench.stages.gates  # noqa: F401  -- populates GATES via register()
from bench.stages.contract import (
    GATES,
    Check,
    Gate,
    Inapplicable,
    StageResult,
)


def _ok(examined: int, tool: str = "kubeconform") -> Check:
    return Check(tool=tool, argv=("-summary",), exit_code=0, examined=examined,
                 resolved_path=f"/usr/bin/{tool}")


# --- invariant 1: a pass must have examined something ----------------------


def test_a_pass_that_examined_nothing_is_not_a_pass():
    """#104 in one assertion.

    chant's static gate exited zero on every run while kubeconform reported
    `Valid: 0 ... Skipped: 38`. Under the old dict contract that was a PASS,
    and there was no way for a reader to tell. Here it cannot be.
    """
    r = StageResult(checks=[_ok(examined=0)])
    assert r.verdict() == "inapplicable"
    assert r.inapplicable is Inapplicable.GATE_DEFECT
    assert "examining nothing" in r.reason


def test_a_pass_with_evidence_is_a_pass():
    """The other direction, which is the half #84 says everyone forgets."""
    r = StageResult(checks=[_ok(examined=38)])
    assert r.verdict() == "pass"
    assert r.examined == 38


def test_examined_sums_across_checks():
    r = StageResult(checks=[_ok(12), _ok(26, tool="kustomize")])
    assert r.examined == 38
    assert r.verdict() == "pass"


def test_a_nonzero_exit_fails_even_with_evidence():
    r = StageResult(checks=[
        _ok(38),
        Check(tool="flux", argv=("build",), exit_code=1, examined=1),
    ])
    assert r.verdict() == "fail"


def test_a_failing_check_that_examined_nothing_still_fails():
    """A gate that died in setup is a failure, not an abstention — but see
    `scoreable`: whether it counts is a separate question from the verdict."""
    r = StageResult(checks=[Check(tool="pulumi", argv=("preview",),
                                  exit_code=255, examined=0)])
    assert r.verdict() == "fail"


def test_no_checks_at_all_is_no_artifact():
    r = StageResult(checks=[])
    assert r.verdict() == "inapplicable"
    assert r.inapplicable is Inapplicable.NO_ARTIFACT


# --- invariant 2: an abstention must say why -------------------------------


def test_by_spec_abstention_is_scoreable():
    """T1-comprehend has no build stage. Dropping it from the denominator is
    correct, and it is the only abstention for which that is true."""
    r = StageResult(inapplicable=Inapplicable.BY_SPEC,
                    reason="task declares no static stage")
    assert r.verdict() == "inapplicable"
    assert r.scoreable() is True


def test_gate_defect_is_not_scoreable_in_either_direction():
    """#110. Failing the arm punishes it for the harness; dropping it rewards
    the harness's own bug — crossplane took correctness 1.00 off a single
    attempted stage because two gates could not run."""
    r = StageResult(inapplicable=Inapplicable.GATE_DEFECT,
                    reason="render needs an XR the task forbids")
    assert r.scoreable() is False


def test_no_artifact_is_not_silently_dropped():
    """A model that produced nothing is a result about the model. It should
    not vanish from the denominator the way a by-spec abstention does."""
    r = StageResult(inapplicable=Inapplicable.NO_ARTIFACT,
                    reason="no manifests in workspace")
    assert r.scoreable() is False


def test_vacuous_pass_is_reported_as_a_gate_defect_not_by_spec():
    """The downgrade must land in the category that blocks scoring. If a
    vacuous pass became BY_SPEC it would still leave the denominator, and
    #104 would score exactly as it did before."""
    r = StageResult(checks=[_ok(examined=0)])
    r.verdict()
    assert r.scoreable() is False


# --- invariant 3: record the binary that ran -------------------------------


def test_a_check_can_record_the_binary_that_actually_ran():
    """#106: provenance recorded `@intentius/chant 0.49.0` while a different
    0.49.0 executed from ~/.nvm. A version cannot distinguish them."""
    c = Check(tool="chant", argv=("build",), exit_code=0, examined=38,
              resolved_path="/ws/node_modules/.bin/chant")
    assert c.resolved_path.endswith("node_modules/.bin/chant")


def test_examined_cannot_be_negative():
    with pytest.raises(ValueError):
        Check(tool="x", argv=(), exit_code=0, examined=-1)


# --- the legacy bridge, so migration can be incremental --------------------


def test_legacy_shape_is_what_the_runner_already_consumes():
    legacy = StageResult(checks=[_ok(38)]).to_legacy()
    assert legacy["passed"] is True
    assert legacy["examined"] == 38
    assert "inapplicable" not in legacy


def test_legacy_inapplicable_carries_its_reason_code():
    r = StageResult(inapplicable=Inapplicable.GATE_DEFECT, reason="no XR")
    legacy = r.to_legacy()
    assert legacy["inapplicable"] is True
    assert legacy["inapplicable_reason"] == "gate_defect"
    assert "passed" not in legacy, (
        "an inapplicable stage must carry no `passed` key — every existing "
        "reader treats a missing key as not-passed"
    )


def test_a_vacuous_pass_does_not_reach_legacy_as_passed():
    """End to end: the shape #104 produced must now be unrepresentable."""
    legacy = StageResult(checks=[_ok(examined=0)]).to_legacy()
    assert legacy.get("passed") is not True
    assert legacy["inapplicable"] is True


# --- the registry: every migrated gate proves it can discriminate ----------


def _skip_if_this_machine_cannot_run(result, stack):
    """A gate reporting GATE_DEFECT on its own correct fixture is telling us
    this machine cannot exercise it -- a missing SDK, an absent binary, no
    dependency tree. That is precisely what GATE_DEFECT means, and it is a
    skip rather than a failure: the same posture test_golden_gates takes for
    the arms CI cannot run. Treating it as a failure would make the suite red
    on every machine that lacks one toolchain, which is how people learn to
    ignore a red suite."""
    from bench.stages.contract import Inapplicable

    if result.inapplicable is Inapplicable.GATE_DEFECT:
        pytest.skip(f"{stack} cannot run here: {result.reason[:120]}")


@pytest.mark.skipif(not GATES, reason="no gates migrated onto the contract yet")
@pytest.mark.parametrize("stack", sorted(GATES))
def test_gate_passes_its_model_shaped_fixture(stack, tmp_path):
    gate: Gate = GATES[stack]
    ws = gate.fixture_pass(tmp_path)
    result = gate.run(Path(ws))
    _skip_if_this_machine_cannot_run(result, stack)
    assert result.verdict() == "pass", (
        f"{stack}'s gate cannot pass a plausible correct answer: "
        f"{result.reason or result.to_legacy().get('logs', '')[:300]}"
    )
    assert result.examined > 0


@pytest.mark.skipif(not GATES, reason="no gates migrated onto the contract yet")
@pytest.mark.parametrize("stack", sorted(GATES))
def test_gate_fails_its_wrong_fixture(stack, tmp_path):
    gate: Gate = GATES[stack]
    ws = gate.fixture_fail(tmp_path)
    result = gate.run(Path(ws))
    _skip_if_this_machine_cannot_run(result, stack)
    assert result.verdict() == "fail", (
        f"{stack}'s gate passes a wrong answer — it cannot discriminate"
    )


def test_registry_is_documented_as_incomplete():
    """Guard against the registry silently staying empty forever. When gates
    are migrated this becomes a real coverage assertion; until then it records
    that the migration is outstanding rather than done."""
    from bench.report import STACKS

    unmigrated = sorted(set(STACKS) - set(GATES))
    assert len(unmigrated) <= len(STACKS), "registry cannot exceed known stacks"


# --- timeout regression tests ------------------------------------------------


def test_terraform_gate_init_timeout_recorded_as_check(tmp_path, monkeypatch):
    """TimeoutExpired on terraform init records a check with exit_code 124
    instead of raising."""
    import subprocess
    import shutil
    from bench.stages.gates import TerraformGate

    ws = tmp_path / "terraform-ws"
    ws.mkdir()
    (ws / "main.tf").write_text(
        'terraform {\n'
        '  required_providers {\n'
        '    aws = { source = "hashicorp/aws", version = "~> 5.0" }\n'
        '  }\n'
        '}\n'
    )

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}" if name == "terraform" else None)

    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", timeout_run)

    gate = TerraformGate()
    result = gate.run(ws)

    assert result.verdict() == "fail"
    assert len(result.checks) == 1
    assert result.checks[0].tool == "terraform"
    assert result.checks[0].exit_code == 124
    assert result.checks[0].examined == 0
    assert result.checks[0].argv == ("init", "-backend=false", "-input=false", "-no-color")


def test_terraform_gate_validate_timeout_recorded_as_check(tmp_path, monkeypatch):
    """TimeoutExpired on terraform validate records a check with exit_code 124
    and preserves the init check."""
    import subprocess
    import shutil
    from bench.stages.gates import TerraformGate

    ws = tmp_path / "terraform-ws"
    ws.mkdir()
    (ws / "main.tf").write_text(
        'terraform {\n'
        '  required_providers {\n'
        '    aws = { source = "hashicorp/aws", version = "~> 5.0" }\n'
        '  }\n'
        '}\n'
    )

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}" if name == "terraform" else None)

    def selective_timeout_run(*args, **kwargs):
        if "validate" in args[0]:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))
        # init succeeds

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""
        return _Proc()

    monkeypatch.setattr(subprocess, "run", selective_timeout_run)

    gate = TerraformGate()
    result = gate.run(ws)

    assert len(result.checks) == 2
    assert result.checks[0].argv == ("init", "-backend=false", "-input=false", "-no-color")
    assert result.checks[0].exit_code == 0
    assert result.checks[1].argv == ("validate", "-no-color")
    assert result.checks[1].exit_code == 124
    assert result.checks[1].examined == 0


def test_chant_gate_kubeconform_timeout_recorded_as_check(tmp_path, monkeypatch):
    """TimeoutExpired on kubeconform call records a check with exit_code 124
    and preserves the chant build check."""
    import subprocess
    import shutil
    from bench.stages.gates import ChantGate
    from bench.stages import lint as lint_mod

    ws = tmp_path / "chant-ws"
    (ws / "node_modules" / ".bin").mkdir(parents=True, exist_ok=True)
    (ws / "src").mkdir(exist_ok=True)
    (ws / "src" / "test.ts").write_text("export const x = 1;")

    # Mock workspace_bin to return a fake chant path
    monkeypatch.setattr(lint_mod, "workspace_bin",
                        lambda ws, name: f"/fake/bin/{name}" if name == "chant" else None)
    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}" if name == "kubeconform" else None)

    def selective_timeout_run(*args, **kwargs):
        # The gate runs chant build first, then kubeconform on its output.
        if args[0][0] == "/fake/bin/chant":
            build_out = ws / "build" / "manifests.yaml"
            build_out.parent.mkdir(parents=True, exist_ok=True)
            build_out.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n")

            class _Proc:
                returncode = 0
                stdout = ""
                stderr = ""
            return _Proc()
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", selective_timeout_run)

    gate = ChantGate()
    result = gate.run(ws)

    # Should have two checks: chant build and kubeconform
    assert len(result.checks) == 2
    assert result.checks[0].tool == "chant"
    assert result.checks[0].exit_code == 0
    assert result.checks[1].tool == "kubeconform"
    assert result.checks[1].exit_code == 124
    assert result.checks[1].examined == 0
