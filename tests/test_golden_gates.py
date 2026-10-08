"""Every stack's gates must pass that stack's own golden base.

This is the assertion whose absence let #81, #82 and a pile of invalid golden
YAML survive a green suite for months. Every other test in this repo checks
that bad input is caught. None checked that *good* input is not — and a gate
that fails everything is indistinguishable from a stack whose model output is
always wrong, right up until someone publishes the difference as a finding.

golden-base/<stack> is a correct answer by construction. So:

    lint(golden)   must pass
    static(golden) must pass

A failure here is a broken gate or a broken golden. Both are harness defects
and neither is ever a model result.

Stacks that cannot satisfy this yet are marked xfail with their issue, so that
fixing one turns into an XPASS someone has to notice rather than a quiet
nothing.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from bench.preflight import STACK_BINARIES
from bench.stages import lint as lint_mod
from bench.stages import static as static_mod

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "golden-base"

STACKS = ["knr-ops", "crossplane", "terraform", "pulumi-python",
          "pulumi-typescript", "chant", "bare"]

# stack -> why it cannot pass yet
# Empty, and it must stay that way: every stack passes its own golden. An entry
# here is a stack whose numbers cannot be trusted, so adding one is a decision
# to publish an arm nothing can measure.
KNOWN_BROKEN: dict[str, str] = {}




def _ensure_stack_prereqs(stack: str) -> None:
    """Build whatever the golden needs that is not committed.

    golden-base/chant/node_modules is not tracked, so this test passed only on
    a machine where someone had already installed it — a guard that works on
    one machine is not a guard. The repo already has the installer, and it
    works from a committed package-lock.json plus vendored tarballs, so it is
    reproducible on a fresh clone rather than needing a registry.
    """
    if stack != "chant":
        return
    if (GOLDEN / "chant" / "node_modules").is_dir():
        return
    try:
        from bench.stages.e2e import ensure_chant_node_modules
        ensure_chant_node_modules()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"chant node_modules unavailable and could not be built: {exc}")


def _aws_credentials_present() -> bool:
    """Can the AWS provider validate credentials here?

    `pulumi preview` is not offline: the AWS provider calls STS before it will
    preview a resource. Dummy values do not satisfy it, and the provider's
    skipCredentialsValidation setting did not take effect through the gate. So
    the pulumi arms genuinely require real credentials to be *statically*
    checked, which is worth knowing before reading a pulumi verdict as a
    statement about the model.
    """
    import os
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return True
    shared = os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or os.path.expanduser("~/.aws/credentials")
    return Path(shared).is_file()


def _materialize(stack: str) -> Path:
    """Copy the golden the way a run would, preserving symlinks.

    symlinks=True matters: node_modules/.bin entries are symlinks, and
    dereferencing them breaks module resolution in a way that looks exactly
    like a real lint failure.
    """
    ws = Path(tempfile.mkdtemp(prefix=f"golden-{stack}-"))
    shutil.copytree(GOLDEN / stack, ws, dirs_exist_ok=True, symlinks=True)
    return ws


def _verdict(stage: dict) -> str:
    if stage.get("skipped") or stage.get("inapplicable"):
        return "inapplicable"
    return "pass" if stage.get("passed") else "fail"


@pytest.mark.parametrize("stack", STACKS)
def test_golden_passes_its_own_gates(stack):
    if stack in KNOWN_BROKEN:
        pytest.xfail(KNOWN_BROKEN[stack])
    if not (GOLDEN / stack).is_dir():
        pytest.skip(f"no golden-base/{stack}")

    # Skip rather than fail when the toolchain is absent. A missing binary is
    # an environment problem, and reporting it as a gate defect here would
    # bury the real signal in noise — which is the mistake preflight exists to
    # avoid (#88). preflight is what refuses a *run* on missing tools.
    missing = [b for b in STACK_BINARIES.get(stack, ()) if shutil.which(b) is None]
    if missing:
        pytest.skip(f"toolchain missing for {stack}: {missing}")

    if stack.startswith("pulumi-") and not _aws_credentials_present():
        pytest.skip(
            f"{stack} static runs `pulumi preview`, whose AWS provider validates "
            "credentials against STS before previewing anything. Without them the "
            "gate fails for the environment rather than the artifact, so this is "
            "an environment prerequisite like a missing binary — not a gate defect. "
            "See docs/result-integrity.md."
        )

    _ensure_stack_prereqs(stack)

    ws = _materialize(stack)
    try:
        lint_res = lint_mod.run_lint(ws, stack)
        static_res = static_mod.run_static(ws, stack)
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    assert _verdict(lint_res) == "pass", (
        f"{stack} lint fails its own golden — a correct answer scores as wrong.\n"
        + (lint_res.get("logs") or "")[:1500]
    )
    assert _verdict(static_res) == "pass", (
        f"{stack} static fails its own golden — a correct answer scores as wrong.\n"
        + (static_res.get("logs") or "")[:1500]
    )


def test_every_stack_has_a_golden():
    """A stack with no reference implementation cannot be checked this way at
    all, which is how bare and chant went unverified the longest."""
    missing = [s for s in STACKS if not (GOLDEN / s).is_dir()]
    assert not missing, f"no golden-base for: {missing}"


def test_the_known_broken_list_is_not_a_dumping_ground():
    """Every entry needs a reason, and the list must shrink, not grow."""
    assert len(KNOWN_BROKEN) == 0, (
        "a stack has been added to the exemption list; "
        "fix the gate rather than extending the exemption list"
    )
    for stack, reason in KNOWN_BROKEN.items():
        assert stack in STACKS
        assert len(reason) > 20, f"{stack}: give a real reason"
