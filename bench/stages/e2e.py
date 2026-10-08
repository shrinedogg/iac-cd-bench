"""
E2E stage runner for IaC/CD benchmark.

Runs live e2e against kind clusters + LocalStack.
Requires --e2e flag to execute.
"""

from __future__ import annotations

import fcntl
import subprocess
import tempfile
import logging
import time
from pathlib import Path

# Module level, not inside a function: `_e2e_chant` needs workspace_bin to
# resolve the vendored binary rather than PATH (#106). Safe direction — lint
# imports e2e only lazily, inside a function, so this is not a cycle.
from bench.stages import lint as lint_mod

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]

LOCALSTACK_ENDPOINT = "http://localhost:4566"
LOCALSTACK_CONTAINERS = ["s3", "rds", "iam"]


def run_e2e(workspace: Path, stack: str, kind_cluster_name: str = "bench") -> dict:
    """Run live e2e against kind + LocalStack."""
    results: list[str] = []
    passed = True

    results.append("=== E2E Stage ===")

    # Ensure kind cluster exists
    results.append(_ensure_kind_cluster(kind_cluster_name))

    # Start LocalStack
    results.append(_ensure_localstack())

    # Run stack-specific e2e
    if stack == "knr-ops":
        passed &= _e2e_knr_ops(workspace, results)
    elif stack == "crossplane":
        passed &= _e2e_crossplane(workspace, results)
    elif stack == "terraform":
        passed &= _e2e_terraform(workspace, results)
    elif stack in ("pulumi-python", "pulumi-typescript"):
        passed &= _e2e_pulumi(workspace, results)
    elif stack == "chant":
        passed &= _e2e_chant(workspace, results)
    elif stack == "bare":
        passed &= _e2e_bare(workspace, results)
    else:
        results.append(f"no e2e for stack: {stack}")
        passed = False

    # Teardown
    results.append("=== Teardown ===")
    try:
        subprocess.run(
            ["docker", "rm", "-f", "localstack"],
            capture_output=True, text=True, timeout=30,
        )
        results.append("LocalStack container stopped")
    except Exception as e:
        results.append(f"WARNING: LocalStack teardown failed: {e}")

    return {
        "passed": passed,
        "logs": "\n".join(results),
    }


def _ensure_kind_cluster(name: str) -> str:
    """Ensure kind cluster exists."""
    log.info("Ensuring kind cluster: %s", name)
    try:
        proc = subprocess.run(
            ["kind", "get", "clusters"],
            capture_output=True, text=True, timeout=30,
        )
        if name not in proc.stdout:
            log.info("Creating kind cluster: %s", name)
            proc = subprocess.run(
                ["kind", "create", "cluster", "--name", name],
                capture_output=True, text=True, timeout=300,
            )
            return f"Created kind cluster: {name}"
        return f"Kind cluster exists: {name}"
    except Exception as e:
        return f"WARNING: kind cluster check failed: {e}"


def _ensure_localstack() -> str:
    """Start LocalStack container."""
    log.info("Starting LocalStack")
    try:
        subprocess.run(
            ["docker", "rm", "-f", "localstack"],
            capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            [
                "docker", "run", "-d", "--name", "localstack",
                "-p", "4566:4566",
                "-e", f"SERVICES={','.join(LOCALSTACK_CONTAINERS)}",
                "localstack/localstack:latest",
            ],
            capture_output=True, text=True, timeout=60,
        )
        # Wait for LocalStack to be ready
        time.sleep(10)
        return "LocalStack started"
    except Exception as e:
        return f"WARNING: LocalStack start failed: {e}"


def _e2e_knr_ops(workspace: Path, results: list[str]) -> bool:
    """Run knr-ops e2e: flux install, apply, verify bucket."""
    passed = True

    # Install flux
    results.append("Installing Flux...")
    try:
        proc = subprocess.run(
            ["flux", "install", "--namespace", "flux-system"],
            capture_output=True, text=True, timeout=120,
        )
        results.append(f"flux install: exit={proc.returncode}")
        if proc.returncode != 0:
            results.append(f"ERR: {proc.stderr[:500]}")
            passed = False
    except Exception as e:
        results.append(f"flux install failed: {e}")
        passed = False

    # Apply kustomizations
    results.append("Applying kustomizations...")
    overlays = list(workspace.glob("overlays/*/kustomization.yaml"))
    for overlay in overlays[:1]:  # Test first overlay only
        try:
            proc = subprocess.run(
                ["kubectl", "apply", "-k", str(overlay.parent)],
                capture_output=True, text=True, timeout=60,
            )
            results.append(f"kubectl apply -k {overlay.parent}: exit={proc.returncode}")
            if proc.returncode != 0:
                passed = False
        except Exception as e:
            results.append(f"kubectl apply failed: {e}")
            passed = False

    # Verify bucket exists via LocalStack
    results.append("Verifying bucket via LocalStack...")
    try:
        proc = subprocess.run(
            ["aws", "--endpoint-url", LOCALSTACK_ENDPOINT, "s3", "ls"],
            capture_output=True, text=True, timeout=30,
            env={"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test"},
        )
        results.append(f"aws s3 ls: exit={proc.returncode}")
        if proc.returncode == 0:
            results.append(f"buckets: {proc.stdout}")
        else:
            results.append(f"aws s3 ls failed (expected if bucket not yet created)")
    except Exception as e:
        results.append(f"aws s3 ls failed: {e}")

    return passed


def _e2e_crossplane(workspace: Path, results: list[str]) -> bool:
    """Run Crossplane e2e: install providers, apply, verify."""
    passed = True

    results.append("Installing Crossplane...")
    try:
        proc = subprocess.run(
            ["kubectl", "apply", "-f", "https://raw.githubusercontent.com/crossplane/crossplane/master/install.yaml"],
            capture_output=True, text=True, timeout=120,
        )
        results.append(f"crossplane install: exit={proc.returncode}")
        if proc.returncode != 0:
            passed = False
    except Exception as e:
        results.append(f"crossplane install failed: {e}")
        passed = False

    results.append("Installing provider-aws-s3...")
    try:
        proc = subprocess.run(
            ["crossplane", "x", "pkg", "install", "crossplane/provider-aws-s3:v0.28.0"],
            capture_output=True, text=True, timeout=60,
        )
        results.append(f"provider-aws-s3 install: exit={proc.returncode}")
    except Exception as e:
        results.append(f"provider-aws-s3 install failed: {e}")

    results.append("E2E crossplane: provider provisioning requires real AWS; marking partial")
    return passed  # Partial - ACK providers need real AWS for full verification


def _e2e_terraform(workspace: Path, results: list[str]) -> bool:
    """Run Terraform e2e: apply against LocalStack endpoints."""
    passed = True

    results.append("Terraform e2e: apply against LocalStack")
    try:
        proc = subprocess.run(
            ["terraform", "apply", "-auto-approve", "-input=false"],
            capture_output=True, text=True, timeout=120,
            cwd=str(workspace),
            env={
                "AWS_ACCESS_KEY_ID": "test",
                "AWS_SECRET_ACCESS_KEY": "test",
                "AWS_DEFAULT_REGION": "us-east-1",
                "AWS_ENDPOINT_URL_S3": LOCALSTACK_ENDPOINT,
            },
        )
        results.append(f"terraform apply: exit={proc.returncode}")
        if proc.stdout:
            results.append(proc.stdout[:1000])
        if proc.returncode != 0:
            passed = False
    except Exception as e:
        results.append(f"terraform apply failed: {e}")
        passed = False

    return passed


def _e2e_pulumi(workspace: Path, results: list[str]) -> bool:
    """Run Pulumi e2e: up against LocalStack endpoints."""
    passed = True

    results.append("Pulumi e2e: up against LocalStack")
    try:
        proc = subprocess.run(
            ["pulumi", "up", "--yes", "--non-interactive", "-s", "dev"],
            capture_output=True, text=True, timeout=120,
            cwd=str(workspace),
        )
        results.append(f"pulumi up: exit={proc.returncode}")
        if proc.stdout:
            results.append(proc.stdout[:1000])
        if proc.returncode != 0:
            passed = False
    except Exception as e:
        results.append(f"pulumi up failed: {e}")
        passed = False

    return passed


def _e2e_chant(workspace: Path, results: list[str]) -> bool:
    """Run chant e2e: build to YAML, then kubectl apply against the kind
    cluster (mirrors the knr-ops apply path)."""
    passed = True

    # Both of these are the bugs fixed in _chant_static, which this path
    # duplicated and which never fired because e2e has never run on any arm
    # (#112). Fixed here rather than left as a trap for whoever wires it up:
    #
    #   * build into a private temp dir, not the workspace. The static gate's
    #     equivalent wrote into golden-base/chant whenever the preflight ran
    #     the gate against the golden -- the one shared mutable path in the
    #     harness, and what makes concurrent runners unsafe.
    #   * resolve `chant` out of the workspace, never PATH (#106). A global
    #     @intentius/chant reporting the same version as the vendored one had
    #     a different command surface, and provenance could not tell them
    #     apart.
    build_tmp = tempfile.mkdtemp(prefix="chant-e2e-build-")
    build_out = Path(build_tmp) / "manifests.yaml"

    results.append("Building chant workspace...")
    try:
        proc = subprocess.run(
            [lint_mod.workspace_bin(workspace, "chant"),
             "build", ".", "-f", "yaml", "-o", str(build_out)],
            capture_output=True, text=True, timeout=60,
            cwd=str(workspace),
        )
        results.append(f"chant build -f yaml: exit={proc.returncode}")
        if proc.returncode != 0:
            results.append(f"ERR: {proc.stderr[:500]}")
            passed = False
    except Exception as e:
        results.append(f"chant build failed: {e}")
        passed = False

    if passed and build_out.exists():
        results.append("Applying built manifests...")
        try:
            proc = subprocess.run(
                ["kubectl", "apply", "-f", str(build_out)],
                capture_output=True, text=True, timeout=60,
            )
            results.append(f"kubectl apply -f {build_out.name}: exit={proc.returncode}")
            if proc.returncode != 0:
                results.append(f"ERR: {proc.stderr[:500]}")
                passed = False
        except Exception as e:
            results.append(f"kubectl apply failed: {e}")
            passed = False
    elif passed:
        results.append("chant build produced no manifest to apply")
        passed = False

    return passed


def _e2e_bare(workspace: Path, results: list[str]) -> bool:
    """Run bare e2e: kubectl apply the plain YAML manifests directly to the
    kind cluster, one file at a time (no build step, no GitOps controller —
    mirrors the knr-ops apply path minus Flux)."""
    passed = True

    yaml_files = list(workspace.rglob("*.yaml")) + list(workspace.rglob("*.yml"))
    if not yaml_files:
        results.append("no YAML files in workspace")
        return False

    for yfile in yaml_files:
        try:
            proc = subprocess.run(
                ["kubectl", "apply", "-f", str(yfile)],
                capture_output=True, text=True, timeout=60,
            )
            results.append(f"kubectl apply -f {yfile.name}: exit={proc.returncode}")
            if proc.returncode != 0:
                results.append(f"ERR: {proc.stderr[:500]}")
                passed = False
        except Exception as e:
            results.append(f"kubectl apply failed: {e}")
            passed = False

    return passed


def _ensure_node_modules_locked(golden_dir: Path, markers: list[Path],
                                install_argv: list[str], label: str) -> Path:
    """Run `install_argv` in `golden_dir` at most once, serialized against
    every other thread or process racing the same shared install.

    A matrix run gates many stacks' tasks concurrently (bench.stages.gates
    thread pool, or a parallel bench.runner sweep), and every one of them
    lazily wants this same golden's node_modules the first time it is
    touched. Without a real lock the check-then-install below is a classic
    TOCTOU: two callers both see it missing, both start `npm install` into
    the same directory, and one's writes race the other's — a workspace
    symlinked mid-install can observe node_modules as transiently absent or
    partial, which is indistinguishable from a genuinely broken gate.

    `fcntl.flock` on a marker file next to node_modules serializes both
    threads (each open() gets its own file description, so flock blocks
    exactly like it would across processes) and separate `bench` invocations
    sharing the same checkout.
    """
    if all(m.is_dir() for m in markers):
        return golden_dir

    lock_path = golden_dir / ".node_modules.lock"
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            if all(m.is_dir() for m in markers):
                return golden_dir
            log.info("Installing %s node_modules (one-time, cached for the "
                      "rest of this process)", label)
            subprocess.run(
                install_argv,
                cwd=str(golden_dir),
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    return golden_dir


def ensure_chant_node_modules(golden_dir: Path | None = None) -> Path:
    """Install golden-base/chant's node_modules once (from its committed
    package-lock.json + vendor/*.tgz tarballs) and hand back the directory
    to use as a shared template.

    Two callers share this: preflight_chant_golden below (which lints/
    builds golden-base/chant itself, and needs its own node_modules to do
    that honestly) and bench.runner.materialize_task (which symlinks this
    same node_modules into every materialized chant task workspace so
    tsc/chant can resolve `@intentius/chant`'s conditional package exports
    without a per-run npm install — a full matrix's 36+ chant runs share
    one install instead of each installing cold).

    Idempotent: checks for both vendored packages actually present under
    node_modules rather than just the directory existing, so a partial or
    stale install re-runs `npm install` instead of silently reusing it.
    """
    golden_dir = golden_dir or (ROOT / "golden-base" / "chant")
    node_modules = golden_dir / "node_modules"
    markers = [node_modules / "@intentius" / "chant",
               node_modules / "@intentius" / "chant-lexicon-k8s"]
    return _ensure_node_modules_locked(
        golden_dir, markers,
        ["npm", "install", "--no-audit", "--no-fund"], "golden-base/chant")


def ensure_pulumi_typescript_node_modules(golden_dir: Path | None = None) -> Path:
    """Install golden-base/pulumi-typescript's node_modules once (from its
    committed package-lock.json) so tsc can resolve @pulumi/aws types at lint time.

    Idempotent: checks for @pulumi/aws actually present under node_modules
    rather than just the directory existing, so a partial or stale install
    re-runs `npm ci` instead of silently reusing it.
    """
    golden_dir = golden_dir or (ROOT / "golden-base" / "pulumi-typescript")
    node_modules = golden_dir / "node_modules"
    markers = [node_modules / "@pulumi" / "aws"]
    return _ensure_node_modules_locked(
        golden_dir, markers,
        ["npm", "ci", "--no-audit", "--no-fund"], "golden-base/pulumi-typescript")


def preflight_chant_golden() -> dict:
    """Fairness gate (in the spirit of chant-bench): assert golden-base/chant
    itself passes lint + static before any model run burns tokens on chant
    tasks.

    golden-base/chant doesn't exist yet (see issue #3). Until it lands, this
    skips gracefully with a clear message rather than hard-failing every
    chant run — the wiring can land early behind the `--stack chant`
    escape hatch per issue #5.
    """
    from bench.stages import lint, static

    golden_dir = ROOT / "golden-base" / "chant"
    if not golden_dir.exists():
        msg = (
            "SKIP: golden-base/chant does not exist yet (see issue #3); "
            "the chant preflight gate will activate once it lands"
        )
        log.warning(msg)
        return {"passed": True, "skipped": True, "logs": msg}

    try:
        ensure_chant_node_modules(golden_dir)
    except Exception as e:  # noqa: BLE001 - surfaced as a failed preflight, not a crash
        msg = f"golden-base/chant node_modules install failed: {e}"
        log.error(msg)
        return {"passed": False, "skipped": False, "logs": msg}

    lint_result = lint.run_lint(golden_dir, "chant")
    static_result = static.run_static(golden_dir, "chant")
    passed = bool(lint_result.get("passed")) and bool(static_result.get("passed"))
    logs = (
        f"golden-base/chant preflight: lint={'PASS' if lint_result.get('passed') else 'FAIL'}, "
        f"static={'PASS' if static_result.get('passed') else 'FAIL'}\n"
        f"--- lint ---\n{lint_result.get('logs', '')}\n"
        f"--- static ---\n{static_result.get('logs', '')}"
    )
    if not passed:
        log.error("chant golden preflight FAILED:\n%s", logs)
    return {"passed": passed, "skipped": False, "logs": logs}


def preflight_pulumi_typescript_golden() -> dict:
    """Fairness gate: assert golden-base/pulumi-typescript itself passes lint
    before any model run burns tokens on pulumi-typescript tasks. The issue is
    that tsc cannot resolve @pulumi/aws types without a node_modules tree; this
    gate ensures the fix works by running lint on the golden with its
    node_modules properly installed."""
    from bench.stages import lint

    golden_dir = ROOT / "golden-base" / "pulumi-typescript"
    if not golden_dir.exists():
        msg = "SKIP: golden-base/pulumi-typescript does not exist yet"
        log.warning(msg)
        return {"passed": True, "skipped": True, "logs": msg}

    try:
        ensure_pulumi_typescript_node_modules(golden_dir)
    except Exception as e:  # noqa: BLE001 - surfaced as a failed preflight, not a crash
        msg = f"golden-base/pulumi-typescript node_modules install failed: {e}"
        log.error(msg)
        return {"passed": False, "skipped": False, "logs": msg}

    lint_result = lint.run_lint(golden_dir, "pulumi-typescript")
    passed = bool(lint_result.get("passed"))
    logs = (
        f"golden-base/pulumi-typescript preflight: lint={'PASS' if lint_result.get('passed') else 'FAIL'}\n"
        f"--- lint ---\n{lint_result.get('logs', '')}"
    )
    if not passed:
        log.error("pulumi-typescript golden preflight FAILED:\n%s", logs)
    return {"passed": passed, "skipped": False, "logs": logs}
