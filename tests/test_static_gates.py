"""Static-gate tests for the bare and knr-ops arms (#81, #82, #83).

Both gates used to be incapable of passing, for different reasons, and no
test caught it because no test asserted that a correct answer *passes*. A
gate that fails everything looks identical to a stack that gets everything
wrong, right up until someone publishes the difference as a finding.

So the load-bearing assertion in this file is the boring one: a known-good
manifest set produces `passed: True`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bench.stages.static import SCHEMA_DIR, _bare_static, run_static

# The ACK resources bare's task fixtures actually use, spelled correctly.
GOOD_BARE = """\
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata:
  name: myapp-logs-prod
spec:
  name: myapp-logs-prod
---
apiVersion: iam.services.k8s.aws/v1alpha1
kind: Policy
metadata:
  name: myapp-logs-access-prod
spec:
  name: myapp-logs-access-prod
  policyDocument: '{"Version":"2012-10-17","Statement":[]}'
"""

# `RolePolicyAttachment` is not an ACK IAM kind. Models in the 3arm-v3 matrix
# emitted it, along with UserPolicy and RolePolicy. With schemas vendored and
# -ignore-missing-schemas off, an invented kind has to fail.
INVENTED_KIND = """\
apiVersion: iam.services.k8s.aws/v1alpha1
kind: RolePolicyAttachment
metadata:
  name: myapp-logs-attach
spec:
  roleName: myapp-logs-role
"""

pytestmark = pytest.mark.skipif(
    shutil.which("kubeconform") is None, reason="kubeconform not on PATH"
)


def _run(tmp_path: Path, text: str, name: str = "manifest.yaml"):
    (tmp_path / name).write_text(text)
    results: list[str] = []
    return _bare_static(tmp_path, results), results


def test_vendored_schemas_are_present():
    """The gate is only as good as its schema mirror; an empty schemas/ would
    turn every CRD into a failure and look like a model problem (#83)."""
    assert (SCHEMA_DIR / "s3.services.k8s.aws" / "bucket_v1alpha1.json").is_file()
    assert (SCHEMA_DIR / "iam.services.k8s.aws" / "policy_v1alpha1.json").is_file()


def test_a_correct_bare_answer_passes(tmp_path):
    """The assertion whose absence let #81 stand: the gate can pass."""
    (passed, acted), results = _run(tmp_path, GOOD_BARE)

    assert acted, f"gate abstained on a valid answer: {results}"
    assert passed, f"gate failed a valid answer: {results}"


def test_invented_crd_kind_fails(tmp_path):
    """A kind that resolves to no schema is a kind that does not exist."""
    (passed, acted), results = _run(tmp_path, INVENTED_KIND)

    assert acted
    assert not passed
    assert any("RolePolicyAttachment" in r for r in results)


def test_bare_gate_needs_no_cluster(tmp_path):
    """#81 in one line: the stage must not depend on an API server. If this
    ever regresses to kubectl, the logs carry the connection error again."""
    (passed, _acted), results = _run(tmp_path, GOOD_BARE)

    blob = "\n".join(results)
    assert passed
    assert "connection refused" not in blob
    assert "openapi" not in blob.lower()
    assert "server API group list" not in blob


def test_empty_workspace_is_inapplicable_not_a_pass(tmp_path):
    """A run that produced nothing must not collect a free static pass."""
    stage = run_static(tmp_path, "bare")

    assert stage.get("skipped") or stage.get("inapplicable")
    assert not stage.get("passed")


# --- chant (#104) -----------------------------------------------------------
#
# chant's gate passed `-ignore-missing-schemas` and no `-schema-location` at
# all. Every kind chant emits is a CRD -- ACK, CAPI/CAPA, Flux, not one core
# Kubernetes kind -- so nothing resolved, the flag swallowed it, and every run
# ever recorded summarised as `Valid: 0 ... Skipped: 38`. The gate reduced to
# "did `chant build` exit 0".
#
# `Valid: 0` for every input is the same failure as a gate that fails
# everything: it cannot tell a right answer from a wrong one. These tests pin
# the summary, not just the exit code.

# What chant actually emits: Flux delivery objects and v1beta2 CAPI, spelled
# correctly. Under the old invocation all four of these were skipped.
CHANT_SHAPED = """\
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: myapp-dev-infra
  namespace: flux-system
spec:
  interval: 10m
  path: ./dist/dev/infra
  prune: true
  sourceRef:
    kind: GitRepository
    name: myapp-infra
---
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: myapp-infra
  namespace: flux-system
spec:
  interval: 1m
  ref:
    branch: main
  url: https://github.com/example/myapp-infra
---
apiVersion: cluster.x-k8s.io/v1beta2
kind: Cluster
metadata:
  name: myapp-dev
spec:
  clusterNetwork:
    pods:
      cidrBlocks:
        - 192.168.0.0/16
---
apiVersion: eks.services.k8s.aws/v1alpha1
kind: PodIdentityAssociation
metadata:
  name: myapp-reader
spec:
  clusterName: myapp-dev
  namespace: default
  roleARN: arn:aws:iam::000000000000:role/myapp-reader
  serviceAccount: reader
"""


def _kubeconform_summary(tmp_path: Path, text: str) -> str:
    """Run kubeconform exactly as _chant_static now invokes it."""
    import subprocess

    from bench.stages import lint as lint_mod

    f = tmp_path / "manifests.yaml"
    f.write_text(text)
    proc = subprocess.run(
        ["kubeconform", "-summary", *lint_mod.kubeconform_schema_args(), str(f)],
        capture_output=True, text=True, timeout=60,
    )
    return proc.stdout + proc.stderr


def test_chant_gate_passes_the_schema_mirror_and_does_not_ignore_missing():
    """The two-line source fact behind #104. `-ignore-missing-schemas` turns an
    unresolved kind into a silent skip, and with no `-schema-location` every
    CRD is unresolved -- so the pair together validate nothing at all."""
    src = (Path(__file__).resolve().parent.parent
           / "bench" / "stages" / "static.py").read_text()
    body = src.split("def _chant_static(")[1].split("\ndef ")[0]
    # Strip the docstring: it discusses `-ignore-missing-schemas` by name.
    if '"""' in body:
        body = body.split('"""', 2)[2]
    assert "kubeconform_schema_args()" in body, (
        "chant's kubeconform call must point at the vendored mirror, or every "
        "CRD it emits resolves to nothing"
    )
    assert "-ignore-missing-schemas" not in body, (
        "chant's gate must not skip unresolved kinds -- that is what scored an "
        "invented resource as fine (#104), the defect #83 fixed for bare"
    )


def test_flux_and_v1beta2_schemas_are_vendored():
    """Dropping -ignore-missing-schemas is only safe because these exist. If
    the mirror loses them, chant's own golden starts failing on valid Flux."""
    for group, name in [
        ("kustomize.toolkit.fluxcd.io", "kustomization_v1.json"),
        ("helm.toolkit.fluxcd.io", "helmrelease_v2.json"),
        ("source.toolkit.fluxcd.io", "gitrepository_v1.json"),
        ("cluster.x-k8s.io", "cluster_v1beta2.json"),
        ("infrastructure.cluster.x-k8s.io", "awsmanagedcluster_v1beta2.json"),
        ("addons.cluster.x-k8s.io", "helmchartproxy_v1alpha1.json"),
        ("eks.services.k8s.aws", "podidentityassociation_v1alpha1.json"),
    ]:
        assert (SCHEMA_DIR / group / name).is_file(), f"missing schema {group}/{name}"


def test_chant_shaped_manifests_validate_rather_than_skip(tmp_path):
    """The load-bearing one. Before #104 this summarised `Valid: 0 ...
    Skipped: 4`; a gate reporting Valid: 0 for every input cannot discriminate."""
    out = _kubeconform_summary(tmp_path, CHANT_SHAPED)
    assert "Valid: 4" in out, f"expected all four resources validated, got: {out}"
    assert "Skipped: 0" in out, f"nothing should be skipped now, got: {out}"


def test_invented_flux_kind_fails_for_chant(tmp_path):
    """The negative direction, on chant's own delivery surface: a Flux kind
    that does not exist has to be an error, not a skip."""
    out = _kubeconform_summary(tmp_path, """\
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: KustomizationSet
metadata:
  name: myapp-dev-infra
spec: {}
""")
    assert "Valid: 0" in out and ("Errors: 1" in out or "Invalid: 1" in out), (
        f"an invented Flux kind must not pass: {out}"
    )


# --- the vendored pin has to be the thing that runs ------------------------


def test_workspace_bin_prefers_the_local_install(tmp_path):
    """`golden-base/chant/vendor/` pins two tarballs so the arm is measured
    against a known build. Shelling out to a bare `chant` ran whatever was
    globally npm-installed instead, and the pin reached only tsc through the
    types. Observed on this machine: a global @intentius/chant reporting
    0.49.0 with no `scenario` command, against a vendored 0.49.0 that has it.
    Same version string, different surface."""
    from bench.stages.lint import workspace_bin

    assert workspace_bin(tmp_path, "chant") == "chant", (
        "with no local install, fall back to PATH rather than failing to launch"
    )

    local = tmp_path / "node_modules" / ".bin"
    local.mkdir(parents=True)
    (local / "chant").write_text("#!/bin/sh\n")
    assert workspace_bin(tmp_path, "chant") == str(local / "chant")


def test_chant_gates_never_invoke_a_bare_chant():
    """The source fact. A literal ["chant", ...] argv is the bug."""
    root = Path(__file__).resolve().parent.parent
    for rel in ("bench/stages/static.py", "bench/stages/lint.py"):
        src = (root / rel).read_text()
        assert '["chant"' not in src and "['chant'" not in src, (
            f"{rel} invokes a bare `chant`, which resolves to the global "
            "install rather than the workspace's vendored pin"
        )


# --- isolation: nothing may write into a shared path ------------------------


def test_no_gate_builds_into_its_workspace():
    """chant's build artifact must land in a temp dir, not the workspace.

    `preflight_chant_golden` runs the static gate against `golden-base/chant`
    ITSELF, so `workspace / "build" / "manifests.yaml"` meant writing into the
    golden — the one genuinely shared mutable path in the harness, and the
    thing that makes two concurrent runners unsafe: both build, and one reads
    what the other is mid-write.

    Nothing outside the gate consumes that artifact, so a temp dir costs
    nothing. `_e2e_chant` had the identical line and was never caught because
    e2e has never run on any arm (#112).
    """
    root = Path(__file__).resolve().parent.parent
    for rel in ("bench/stages/static.py", "bench/stages/e2e.py"):
        src = (root / rel).read_text()
        assert 'workspace / "build"' not in src, (
            f"{rel} builds into the workspace. When the gate runs against "
            "golden-base (the preflight does exactly that) this is a write to "
            "a shared path, and concurrent runners race on it."
        )


def test_node_binaries_are_resolved_out_of_the_workspace():
    """Neither gate may shell out to a bare `chant` (#106).

    A global @intentius/chant reporting the same version as the vendored one
    had a different command surface, and provenance recorded the vendored
    version while the global one executed — indistinguishable after the fact.
    """
    root = Path(__file__).resolve().parent.parent
    for rel in ("bench/stages/static.py", "bench/stages/e2e.py"):
        src = (root / rel).read_text()
        assert '["chant"' not in src and "['chant'" not in src, (
            f"{rel} invokes a bare `chant`, which resolves through PATH to "
            "whatever is globally installed rather than the vendored pin"
        )


def test_workspace_bin_returns_an_absolute_path(tmp_path):
    """Callers run the binary with `cwd=workspace`, so a relative path
    resolves against the workspace instead of the caller and the binary is
    simply not found — surfacing as `NOT FOUND: <tool>`, which reads as a
    missing toolchain rather than a path bug."""
    from bench.stages.lint import workspace_bin

    binp = tmp_path / "node_modules" / ".bin"
    binp.mkdir(parents=True)
    (binp / "chant").write_text("#!/bin/sh\n")
    resolved = workspace_bin(tmp_path, "chant")
    assert Path(resolved).is_absolute(), f"not absolute: {resolved}"
