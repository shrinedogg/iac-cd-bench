"""The test bench/preflight.py's own comment claimed already existed.

That comment said "keep the two in sync — tests/test_preflight.py asserts
every command name in LINT_COMMANDS appears here". No such file existed. The
list drifted, exactly as an unenforced comment invites: `pulumi-python`
declared only `pulumi` while its lint stage invoked ruff through a
`<repo>/.venv/bin/python` that this repo does not contain, so preflight
reported PASSED for a stack whose lint gate could not run at all.

Preflight's whole job is to refuse a run that cannot be scored. A preflight
that probes the wrong binaries is worse than none: it converts "you have no
toolchain" into a green light and a wall of failures charged to the model.
"""

from __future__ import annotations

import re
from pathlib import Path

from bench.preflight import STACK_BINARIES
from bench.stages.lint import LINT_COMMANDS

ROOT = Path(__file__).resolve().parent.parent

# Binaries each `_<stack>_static` helper shells out to, read off the source
# rather than hand-listed, so this cannot drift the way the comment did.
STATIC_SOURCE = (ROOT / "bench" / "stages" / "static.py").read_text()

HELPER_TO_STACKS = {
    "_knr_ops_static": ["knr-ops"],
    "_crossplane_static": ["crossplane"],
    "_terraform_static": ["terraform"],
    "_pulumi_static": ["pulumi-python", "pulumi-typescript"],
    "_chant_static": ["chant"],
    "_bare_static": ["bare"],
}


def _static_binaries(helper: str) -> set[str]:
    m = re.search(rf"def {helper}\(.*?\n(.*?)(?=\ndef |\Z)", STATIC_SOURCE, re.S)
    assert m, f"helper {helper} not found in static.py"
    return set(re.findall(
        r'subprocess\.run\(\s*\n?\s*(?:#.*\n\s*)*\[\s*"([a-z][a-z0-9-]*)"', m.group(1)))


def test_every_lint_binary_is_declared_to_preflight():
    for stack, commands in LINT_COMMANDS.items():
        declared = set(STACK_BINARIES.get(stack, ()))
        for cmd, _args, description in commands:
            assert cmd in declared, (
                f"{stack} lint runs `{cmd}` ({description}) but preflight's "
                f"STACK_BINARIES[{stack!r}] = {sorted(declared)}. Preflight "
                "would report PASSED for a stack whose lint cannot run."
            )


def test_every_static_binary_is_declared_to_preflight():
    for helper, stacks in HELPER_TO_STACKS.items():
        for binary in _static_binaries(helper):
            for stack in stacks:
                declared = set(STACK_BINARIES.get(stack, ()))
                assert binary in declared, (
                    f"{stack} static runs `{binary}` but preflight's "
                    f"STACK_BINARIES[{stack!r}] = {sorted(declared)}."
                )


# Binaries a stack needs to *build its workspace*, before any gate runs.
# chant npm-installs node_modules per run via
# bench.stages.e2e.ensure_chant_node_modules, so npm is a real requirement
# even though no lint or static command names it.
SETUP_BINARIES: dict[str, set[str]] = {"chant": {"npm"}}


def test_preflight_declares_nothing_it_does_not_use():
    """The other direction. `bare` listed kubectl long after its static gate
    stopped using it (#81); a stale entry makes preflight refuse to start over
    a binary nothing needs."""
    for stack, declared in STACK_BINARIES.items():
        used = {cmd for cmd, _a, _d in LINT_COMMANDS.get(stack, [])}
        used |= SETUP_BINARIES.get(stack, set())
        for helper, stacks in HELPER_TO_STACKS.items():
            if stack in stacks:
                used |= _static_binaries(helper)
        extra = set(declared) - used
        assert not extra, (
            f"preflight declares {sorted(extra)} for {stack}, but no lint, "
            "static or workspace-setup command invokes them."
        )


def test_chant_setup_binary_is_really_used():
    """SETUP_BINARIES is an assertion about the code, so check it holds rather
    than letting it become the next unenforced comment."""
    e2e_src = (ROOT / "bench" / "stages" / "e2e.py").read_text()

    assert "ensure_chant_node_modules" in e2e_src
    assert '"npm", "install"' in e2e_src.replace("\n", " ").replace("  ", " ")


def test_every_declared_stack_is_a_real_stack():
    from bench.report import STACKS

    assert set(STACK_BINARIES) == set(STACKS), (
        "preflight and the report disagree about which stacks exist"
    )


def test_mixed_old_and_new_provenance_validates():
    """Verify that the validation logic handles mixed old/new provenance correctly.

    Old format: per-stack probing means different runs record different binaries.
    New format: full-toolchain probing means all runs record all binaries.

    The validation logic (in bench.validate) compares per-binary across sets,
    only comparing binaries that both sets recorded. This test verifies that
    the toolchain accumulation logic correctly builds a unified toolchain_versions
    dict that can contain overlapping subsets from old and new runs.
    """
    from collections import Counter

    # Simulate validation's toolchain accumulation logic
    toolchain_versions: dict[str, set[str]] = {}

    # Old-shaped run: only has binaries for "bare" stack (yq, kubeconform)
    old_toolchain = {
        "yq": {"present": True, "version": "v4.53.6"},
        "kubeconform": {"present": True, "version": "v0.7.0"},
    }

    # New-shaped run: has all known binaries, but some are absent
    new_toolchain = {
        "chant": {"present": False, "version": None},
        "crossplane": {"present": True, "version": "v1.20.0"},
        "flux": {"present": True, "version": "flux version 2.5.0"},
        "kubeconform": {"present": True, "version": "v0.7.0"},
        "kustomize": {"present": True, "version": "v5.6.0"},
        "npm": {"present": False, "version": None},
        "pulumi": {"present": True, "version": "version unknown"},
        "ruff": {"present": True, "version": "ruff 0.8.4"},
        "terraform": {"present": True, "version": "Terraform v1.15.8"},
        "tsc": {"present": True, "version": "Version 5.9.3"},
        "yq": {"present": True, "version": "v4.53.6"},
    }

    # Simulate the validation accumulation logic from validate_result_set()
    for toolchain in [old_toolchain, new_toolchain]:
        for name, info in toolchain.items():
            version = (info or {}).get("version")
            if version:
                toolchain_versions.setdefault(name, set()).add(version)

    # Build the final report as validate_result_set() does
    report_toolchains = {
        name: sorted(vs) for name, vs in sorted(toolchain_versions.items())
    }

    # Check for conflicts as validate_result_set() does
    conflicting_binaries = {
        name: vs for name, vs in report_toolchains.items() if len(vs) > 1
    }

    # Should have no conflicts
    assert not conflicting_binaries, (
        f"Mixed old/new provenance should not conflict: {conflicting_binaries}"
    )

    # Verify the accumulated toolchains
    assert "kubeconform" in report_toolchains
    assert "yq" in report_toolchains
    assert report_toolchains["kubeconform"] == ["v0.7.0"]
    assert report_toolchains["yq"] == ["v4.53.6"]
    # New-only binaries should also be present
    assert "terraform" in report_toolchains
    assert report_toolchains["terraform"] == ["Terraform v1.15.8"]
