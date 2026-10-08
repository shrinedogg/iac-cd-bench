"""
Lint stage runner for IaC/CD benchmark.

Validates syntax, formatting, and basic correctness using stack-native linters.
"""

from __future__ import annotations

import subprocess
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Vendored CRD JSON schemas (#83), mirrored from datreeio/CRDs-catalog at
# 7b1e26ef9deea49293714d204c1a2270aab1178f. Defined here rather than in
# bench.stages.static because static already imports this module; putting them
# the other way round would be a cycle.
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
SCHEMA_TEMPLATE = "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"


def kubeconform_schema_args() -> list[str]:
    """Point kubeconform at the vendored mirror, then its built-in registry."""
    return ["-schema-location", "default",
            "-schema-location", str(SCHEMA_DIR / SCHEMA_TEMPLATE)]


def workspace_bin(workspace: Path, name: str) -> str:
    """Resolve a node CLI to the workspace's own node_modules, not PATH.

    Shelling out to a bare `chant` runs whatever is globally npm-installed on
    the machine. `golden-base/chant/vendor/` pins two tarballs precisely so the
    arm is measured against a known build -- and that pin was reaching only
    tsc, through the types, while `chant lint` and `chant build` ran the global
    install. The two are not interchangeable: a global `@intentius/chant`
    reporting version 0.49.0 was missing `scenario`, a command the vendored
    0.49.0 ships. Same version string, different command surface.

    That is the "it passes here" trap with a reproducibility edge -- results
    depend on a developer's global install, and CI never caught it because the
    chant arm is skipped there for want of the private package.

    Falls back to the bare name when the workspace has no local install, so an
    ephemeral task workspace still runs rather than failing to launch.
    """
    # Absolute, always. Callers run the binary with `cwd=workspace`, so a
    # relative path here resolves against the workspace instead of the caller's
    # directory and the binary is simply not found -- silently, as
    # "NOT FOUND: <tool>", which reads like a missing toolchain rather than a
    # path bug. Real runs use absolute temp workspaces so it never surfaced;
    # anything passing a relative path (a test, a probe, a parallel worker with
    # its own root) hits it immediately.
    local = (workspace / "node_modules" / ".bin" / name).resolve()
    return str(local) if local.exists() else name


def is_k8s_manifest(path: Path) -> bool:
    """Does this YAML file contain at least one Kubernetes object?

    kubeconform is handed a file list, and a workspace legitimately contains
    YAML that is not a manifest — `.sops.yaml`, CI config, a Helm values file.
    Feeding those in fails the whole gate on `missing 'kind' key`, which is
    what the knr-ops golden did to its own lint stage. Judge a file by whether
    it declares apiVersion and kind, not by its extension.

    A kustomization.yaml is the counter-example that looks like a manifest:
    it declares `apiVersion: kustomize.config.k8s.io/...` and
    `kind: Kustomization`, but it is a build input, not a cluster object.
    kubeconform has no schema for it and never will, so feeding it in produces
    a permanent skip that pads the skipped count and hides real ones -- knr-ops
    lint reported Valid=2 Skipped=15, and 9 of those skips were this file.
    """
    try:
        docs = [d for d in yaml.safe_load_all(path.read_text())
                if isinstance(d, dict) and "apiVersion" in d and "kind" in d]
    except (OSError, yaml.YAMLError):
        # Unreadable or unparseable: let kubeconform be the one to say so.
        return True
    return any(
        not str(d.get("apiVersion", "")).startswith("kustomize.config.k8s.io")
        for d in docs
    )

LINT_COMMANDS: dict[str, list[tuple[str, list[str], str]]] = {
    "knr-ops": [
        ("yq", ["eval", ".", "--"], "parse all YAML"),
        ("kubeconform", ["-summary", "-ignore-missing-schemas"], "validate CRDs"),
    ],
    "crossplane": [
        ("kubeconform", ["-summary", "-ignore-missing-schemas"], "validate CRDs"),
    ],
    "terraform": [
        ("terraform", ["init", "-backend=false", "-input=false"], "init"),
        ("terraform", ["validate"], "validate"),
    ],
    "pulumi-python": [
        # ruff from PATH. This used to resolve <repo>/.venv/bin/python, a venv
        # that does not exist in this repo, so the gate reported NOT FOUND on
        # every run and could never pass — preflight did not catch it because
        # STACK_BINARIES listed only `pulumi`.
        ("ruff", ["check", "--select", "E,F", "."], "ruff check"),
    ],
    "pulumi-typescript": [
        ("tsc", ["--noEmit", "--skipLibCheck"], "tsc check"),
    ],
    "chant": [
        ("chant", ["lint", "."], "chant lint"),
        ("tsc", ["--noEmit", "--skipLibCheck"], "tsc check"),
    ],
    "bare": [
        ("yq", ["eval", ".", "--"], "parse all YAML"),
        ("kubeconform", ["-summary", "-ignore-missing-schemas"], "validate CRDs"),
    ],
}


def inapplicable(reason: str, reason_code: str = "no_artifact") -> dict:
    """A stage that had nothing to act on did not pass — it did not run.

    Recording `passed: True` for "no YAML files in workspace" is the
    vacuous-pass bug: the runs that produce no artifacts at all are exactly
    the most broken ones, and they collected free passes on lint and static
    while only semantic failed, inflating every gate rate in their favour.
    An inapplicable stage carries no `passed` key at all, so every existing
    `.get("passed", False)` reader treats it as not-passed, and
    bench.score.compute_score drops it from the correctness denominator
    rather than crediting it.

    `reason_code` says WHY, which is the whole of #110. Dropping every
    abstention identically means a gate that cannot run raises the arm's score,
    because correctness is passed/attempted and an abstention leaves the
    denominator. The three cases are not interchangeable:

      by_spec       the task declares no such stage. Legitimate.
      no_artifact   the model produced nothing to check. A result about the
                    MODEL, and the default here because that is what most of
                    these call sites mean.
      gate_defect   the harness could not run. Not scoreable in either
                    direction -- failing the arm punishes it for the harness,
                    dropping it silently rewards the bug.

    See bench.stages.contract.Inapplicable, which is the same vocabulary for
    gates that have been migrated onto the contract.
    """
    return {"inapplicable": True, "reason": reason, "logs": reason,
            "inapplicable_reason": reason_code}


def run_lint(workspace: Path, stack: str) -> dict:
    """Run lint checks for the stack."""
    commands = LINT_COMMANDS.get(stack, [])
    if not commands:
        return inapplicable(f"no lint commands for stack: {stack}", "gate_defect")

    results: list[str] = []
    all_passed = True

    # pulumi-typescript needs node_modules installed for tsc to resolve @pulumi/aws types
    # Only attempt installation if package-lock.json exists (i.e., this is not an empty workspace)
    if stack == "pulumi-typescript" and (workspace / "package-lock.json").exists():
        try:
            from bench.stages import e2e
            e2e.ensure_pulumi_typescript_node_modules(workspace)
        except Exception as e:  # noqa: BLE001
            return {
                "passed": False,
                "logs": f"Failed to install pulumi-typescript node_modules: {e}",
            }

    # Find files for YAML stacks
    yaml_files = list(workspace.rglob("*.yaml")) + list(workspace.rglob("*.yml"))
    if stack in ("knr-ops", "crossplane", "bare"):
        files = [str(f) for f in yaml_files]
        if not files:
            return inapplicable("no YAML files in workspace")
    elif stack in ("pulumi-typescript", "chant"):
        files = [str(f) for f in workspace.rglob("*.ts")]
        if not files:
            return inapplicable("no TypeScript files in workspace")
    else:
        files = ["."]

    has_tsconfig = (workspace / "tsconfig.json").exists()

    for cmd, args, description in commands:
        if cmd == "tsc" and has_tsconfig:
            # A real tsconfig.json (e.g. golden-base/chant, which carries
            # "moduleResolution": "NodeNext" for @intentius/chant's
            # conditional-exports package.json) must be honored via -p;
            # invoking tsc on a bare file list uses tsc's classic-resolution
            # default and cannot resolve those imports, which would silently
            # look like a real type-check failure instead of an invocation
            # bug. Ephemeral task workspaces (no tsconfig.json copied into
            # seed/) fall through to the explicit-file-list invocation below,
            # unchanged.
            cmd_args: list[str] = [cmd, "-p", "tsconfig.json", "--noEmit"]
        elif cmd == "kubeconform":
            # Manifests only (#F6), and against the vendored schema mirror so
            # the CRDs these stacks are actually about get validated instead
            # of skipped (#F7): knr-ops lint on its own golden reported
            # `Valid: 3, Skipped: 23` before this.
            manifests = [f for f in files if is_k8s_manifest(Path(f))]
            if not manifests:
                results.append(f"[{description}] no Kubernetes manifests to validate")
                continue
            cmd_args = [cmd] + args + kubeconform_schema_args() + manifests
        elif stack in ("knr-ops", "crossplane", "pulumi-typescript", "bare"):
            cmd_args = [cmd] + args + files
        elif stack == "chant":
            # "chant lint ." already targets the workspace itself; only tsc
            # (which needs explicit inputs) gets the discovered .ts files.
            # Both resolve to the workspace's own install so the vendored pin
            # is what gets measured, not a global one.
            exe = workspace_bin(workspace, cmd)
            cmd_args = [exe] + args + files if cmd == "tsc" else [exe] + args
        else:
            cmd_args = [cmd] + args
        log.info("Running lint: %s (%s)", cmd, description)
        try:
            proc = subprocess.run(
                cmd_args,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(workspace),
            )
            results.append(f"[{description}] {cmd}: exit={proc.returncode}")
            if proc.stdout:
                results.append(proc.stdout[:500])
            if proc.stderr:
                results.append(f"ERR: {proc.stderr[:500]}")
            if proc.returncode != 0:
                all_passed = False
        except subprocess.TimeoutExpired:
            results.append(f"TIMEOUT: {cmd} ({description})")
            all_passed = False
        except FileNotFoundError:
            results.append(f"NOT FOUND: {cmd} ({description})")
            log.warning("Command not found: %s", cmd)
            all_passed = False

    return {
        "passed": all_passed,
        "logs": "\n".join(results) if results else "lint passed",
    }
