"""
Tooling-health preflight: refuse to start a run set that cannot be scored.

Failure mode this closes (issue #56, and the 427 historical runs that recorded
`static.passed = True` with a log body of `NOT FOUND: pulumi`): when a stage's
binary is absent, the stage cannot distinguish "the model got it right" from
"nothing was checked". Fixing the stage runners to report `passed=False` on
FileNotFoundError stops the lie per-stage, but it still burns a whole matrix of
API spend to produce a wall of failures. The gate has to run *before* the first
token is spent.

Ported from aws-bench/chant-bench's preflight: the harness probes every binary
the selected stacks will invoke, records name+path+version, and refuses to
start when any is missing. `--allow-missing-tools` is the deliberate escape
hatch, and it stamps the whole result set `partial: true` so the numbers can
never later be quoted as a clean run.

The recorded versions are also the toolchain provenance that makes two result
sets comparable (failure mode 5: two worktrees, different binaries, silently
non-comparable numbers).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent


# Binaries each stack's lint + static stages actually invoke. Derived from
# bench.stages.lint.LINT_COMMANDS and bench.stages.static's per-stack helpers;
# keep the two in sync — tests/test_preflight.py asserts every command name in
# LINT_COMMANDS appears here.
STACK_BINARIES: dict[str, tuple[str, ...]] = {
    "knr-ops": ("yq", "kubeconform", "kustomize", "flux"),
    "crossplane": ("kubeconform", "crossplane"),
    "terraform": ("terraform",),
    "pulumi-python": ("pulumi", "python3", "ruff"),
    "pulumi-typescript": ("tsc", "pulumi", "python3"),
    "chant": ("chant", "tsc", "kubeconform", "npm"),
    # kubectl is gone: bare's static gate validates with kubeconform now (#81).
    "bare": ("yq", "kubeconform"),
}

# Extra binaries the live e2e tier needs, on top of the stack's own.
E2E_BINARIES: tuple[str, ...] = ("kind", "docker", "kubectl")

# How to ask each binary for its version. Anything not listed uses --version.
VERSION_ARGS: dict[str, list[str]] = {
    "kubeconform": ["-v"],
    "kustomize": ["version"],
    "crossplane": ["version", "--client"],
    "kubectl": ["version", "--client"],
}

# Binaries that answer no version subcommand at all. `chant` is one: its CLI
# has no `version` command or `--version` flag, so asking produces an error
# string, and a notice recorded as a version is worse than no version. Read
# from the installed package tree by path instead — aws-bench's rule, learned
# when the chant arm's committed pin read 0.33.1 for weeks while the published
# board had been measured against 0.41.0.
def _chant_package_version() -> str | None:
    """chant's version, read off the installed tree rather than asked for."""
    installed = ROOT / "golden-base" / "chant" / "node_modules" / "@intentius" / "chant" / "package.json"
    try:
        return f"@intentius/chant {json.loads(installed.read_text())['version']}"
    except (OSError, ValueError, KeyError):
        pass
    # Not installed yet (the golden base npm-installs lazily): fall back to
    # the declared dependency range, clearly labelled as a pin rather than an
    # observed version, so a reader cannot mistake the two.
    manifest = ROOT / "golden-base" / "chant" / "package.json"
    try:
        deps = json.loads(manifest.read_text()).get("dependencies") or {}
        pinned = deps.get("@intentius/chant")
        return f"@intentius/chant {pinned} (declared pin, not installed)" if pinned else None
    except (OSError, ValueError):
        return None


PACKAGE_VERSION_PROBES = {"chant": _chant_package_version}

VERSION_TIMEOUT = 20

# Terminal colour codes: a version string carrying escape sequences hashes
# differently from the same version captured without a TTY.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class PreflightError(RuntimeError):
    """Raised when required tooling is missing and no override was given."""

    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def all_known_binaries(include_e2e: bool = False) -> list[str]:
    """Union of all binaries across all stacks, sorted for stable reporting."""
    known: set[str] = set()
    for binaries in STACK_BINARIES.values():
        known.update(binaries)
    if include_e2e:
        known.update(E2E_BINARIES)
    return sorted(known)


def required_binaries(stacks: Iterable[str], include_e2e: bool = False) -> list[str]:
    """Union of binaries the given stacks need, sorted for stable reporting."""
    needed: set[str] = set()
    for stack in stacks:
        needed.update(STACK_BINARIES.get(stack, ()))
    if include_e2e:
        needed.update(E2E_BINARIES)
    return sorted(needed)


def probe_binary(name: str) -> dict[str, Any]:
    """Locate a binary and read its version string.

    A binary that resolves on PATH but whose --version invocation fails is
    still reported as present (some tools exit non-zero on --version); the
    version is then recorded as None rather than the probe being treated as a
    missing tool, because PATH resolution is what the stage runners depend on.
    """
    path = shutil.which(name)
    if path is None:
        return {"present": False, "path": None, "version": None}

    probe = PACKAGE_VERSION_PROBES.get(name)
    if probe is not None:
        return {"present": True, "path": path, "version": probe()}

    args = VERSION_ARGS.get(name, ["--version"])
    version: str | None = None
    try:
        proc = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=VERSION_TIMEOUT,
        )
        raw = _ANSI.sub("", proc.stdout or proc.stderr or "").strip()
        first = raw.splitlines()[0].strip() if raw else ""
        # A tool that rejects the flag prints an error; recording that as the
        # version would be worse than recording nothing, because a reader
        # cannot tell the difference and a fingerprint would move with the
        # wording of the error.
        if first and proc.returncode == 0 and not first.lower().startswith("error"):
            version = first[:200]
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("Version probe failed for %s: %s", name, e)

    return {"present": True, "path": path, "version": version}


def probe_toolchain(include_e2e: bool = False) -> dict[str, Any]:
    """Probe every known binary across all stacks. No refusal, just facts.

    Always probes the full toolchain regardless of which stacks will actually
    be run, so that fingerprints are uniformly shaped and comparable by
    construction. A missing binary for a stack you are not running must still
    not block the run — that gate is enforced only on required binaries in
    check(), not here.
    """
    return {name: probe_binary(name) for name in all_known_binaries(include_e2e)}


def check(
    stacks: Iterable[str],
    include_e2e: bool = False,
    allow_missing: bool = False,
) -> dict[str, Any]:
    """Run the tooling-health preflight and return its report.

    Probes the full toolchain (all known binaries) regardless of which stacks
    will be run. Raises PreflightError when a binary required by the targeted
    stacks is absent and `allow_missing` is False. Missing binaries for stacks
    not in the targeted set do not block the run.

    With `allow_missing=True` the report carries `partial: True`, which
    bench.runner stamps onto every run's provenance and bench.validate turns
    into a `partial` verdict for the whole set.
    """
    stacks = list(stacks)
    # Probe the full toolchain regardless of which stacks will run
    toolchain = probe_toolchain(include_e2e)
    # But only fail on binaries required for the targeted stacks
    required = required_binaries(stacks, include_e2e)
    missing_required = sorted(n for n in required if not toolchain[n]["present"])

    report: dict[str, Any] = {
        "stacks": stacks,
        "include_e2e": include_e2e,
        "toolchain": toolchain,
        "missing": missing_required,
        "passed": not missing_required,
        "partial": bool(missing_required) and allow_missing,
        "override": bool(allow_missing),
    }

    if not missing_required:
        log.info(
            "Preflight OK: %d binaries probed (required: %d) for stacks %s",
            len(toolchain), len(required), ", ".join(stacks),
        )
        return report

    detail = ", ".join(missing_required)
    message = (
        f"Preflight FAILED: required binaries missing for stacks "
        f"{', '.join(stacks)}: {detail}. A stage whose binary is absent cannot "
        f"tell a correct answer from an unchecked one — refusing to start. "
        f"Install the tools, drop the affected stacks from --stacks, or pass "
        f"--allow-missing-tools to run anyway (the result set is then marked "
        f"partial and every run in it is rejected by bench.validate)."
    )
    if not allow_missing:
        log.error(message)
        raise PreflightError(message, report)

    log.warning(
        "Preflight OVERRIDDEN (--allow-missing-tools): missing %s. "
        "This result set is marked partial.", detail,
    )
    return report


def format_report(report: dict[str, Any]) -> str:
    """Human-readable preflight summary for logs and the results manifest."""
    lines = [
        f"Preflight for stacks: {', '.join(report.get('stacks', []))}"
        f"{' (+e2e)' if report.get('include_e2e') else ''}",
    ]
    for name, info in sorted(report.get("toolchain", {}).items()):
        if info.get("present"):
            lines.append(f"  OK      {name}: {info.get('version') or 'version unknown'}")
        else:
            lines.append(f"  MISSING {name}")
    if report.get("missing"):
        verdict = "PARTIAL (overridden)" if report.get("partial") else "FAILED"
        lines.append(f"  -> {verdict}: missing {', '.join(report['missing'])}")
    else:
        lines.append("  -> PASSED")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Standalone tooling-health check: `python3 -m bench.preflight --stacks all`."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Check the tooling every selected stack's stages will invoke",
    )
    parser.add_argument("--stacks", default="all",
                        help="Comma-separated stacks or 'all' (default: all)")
    parser.add_argument("--e2e", action="store_true", help="Also check the e2e tier's tools")
    args = parser.parse_args(argv)

    stacks = (
        sorted(STACK_BINARIES) if args.stacks == "all" else args.stacks.split(",")
    )
    try:
        report = check(stacks, include_e2e=args.e2e)
    except PreflightError as e:
        print(format_report(e.report))
        print(f"\n{e}")
        return 1
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
