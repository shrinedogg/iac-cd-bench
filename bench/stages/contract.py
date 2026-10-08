"""The gate contract: what a stage must disclose, and what that guarantees.

Every harness defect found in this repo has one shape — **a stage returned a
verdict without disclosing what it examined.** From `{"passed": True, "logs":
"..."}` you cannot tell whether the gate validated 38 resources or zero,
whether it ran the vendored binary or one from the developer's PATH, whether it
reached the model's artifact or died in setup, or whether "inapplicable" means
"by spec" or "my gate is broken". Each of those ambiguities cost a matrix:

    #104  chant's static gate reported PASS while kubeconform validated 0 of 38
          resources on every run ever recorded. `Valid: 0` for every input
          cannot distinguish a right answer from a wrong one.
    #106  provenance recorded `@intentius/chant 0.49.0` while a *different*
          0.49.0 from ~/.nvm executed. A version string cannot tell them apart;
          a resolved path can.
    #93   pulumi died on an unresolvable stack reference before reading a line
          of the model's program, and logged it as a model failure.
    #109  crossplane's gate demanded an XR the tasks explicitly tell models not
          to write, then abstained when they obeyed.
    #110  an abstention leaves the correctness denominator, so the arm whose
          gate is most broken scores highest.
    #111  four arms have no T3 grader, so they attempt fewer stages and their
          correctness is amplified.

The fix is not another guard bolted into each of the seven per-stack helpers —
that is how `bare` ended up with the zero-validation check since #81 while
chant went without it for months. The fix is one contract that every gate
implements, with the invariants enforced centrally, so a new arm cannot be
added without satisfying them.

## The invariants

1. **A pass must have examined something.** `verdict == "pass"` requires
   `examined > 0`. This alone catches #104 and any future vacuous gate.
2. **An abstention must say why.** `BY_SPEC` is legitimate and may leave the
   correctness denominator. `NO_ARTIFACT` is a real result about the model.
   `GATE_DEFECT` is the harness's own fault and must never be scored as either.
3. **A check records the binary it actually ran**, not the version it believes
   it ran.
4. **Every gate ships a fixture pair** — a model-shaped input that must pass and
   a wrong one that must fail. `test_golden_gates` proves only that the golden
   passes, and the golden is the single input every gate was tuned against;
   it is the weakest test in the suite, not the strongest.

## Migration

This module is additive. `run_static` and `run_lint` still return the legacy
dict, and `StageResult.to_legacy()` produces exactly that shape, so gates can
be moved onto the contract one at a time without a flag day. A gate is migrated
when it returns a `StageResult` and appears in `GATES`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class Inapplicable(Enum):
    """Why a stage produced no verdict. The distinction is load-bearing.

    Collapsing these into one silent "inapplicable" is what let a broken gate
    raise an arm's score (#110): the harness's own defect and a task that
    legitimately has no build stage were recorded identically, and scoring
    dropped both from the denominator.
    """

    BY_SPEC = "by_spec"
    """The task declares no such stage — T1-comprehend has nothing to build.
    Legitimate; may leave the correctness denominator."""

    NO_ARTIFACT = "no_artifact"
    """The model produced nothing this gate could act on. A real result about
    the model, and it should count as one rather than vanish."""

    GATE_DEFECT = "gate_defect"
    """The gate could not run for a reason the model did not cause — a missing
    schema, a tool that is absent, an artifact the task forbids (#109). Never
    scoreable in either direction; it is a bug report, not a measurement."""


@dataclass(frozen=True)
class Check:
    """One thing a gate actually ran, and what it looked at."""

    tool: str
    argv: tuple[str, ...]
    exit_code: int
    examined: int
    """Units of the MODEL'S work this check inspected — resources validated,
    manifests built, assertions evaluated. Not files present, not commands run.
    Zero means the check produced no evidence, whatever its exit code."""
    resolved_path: str | None = None
    """The binary that actually executed. `shutil.which` output or an explicit
    workspace path — never just a name, which is what made #106 invisible."""
    detail: str = ""

    def __post_init__(self) -> None:
        if self.examined < 0:
            raise ValueError("examined cannot be negative")


@dataclass
class StageResult:
    """A stage's verdict, inseparable from the evidence for it."""

    checks: list[Check] = field(default_factory=list)
    inapplicable: Inapplicable | None = None
    reason: str = ""

    @property
    def examined(self) -> int:
        return sum(c.examined for c in self.checks)

    def verdict(self) -> str:
        """pass | fail | inapplicable, with invariant 1 enforced here.

        A pass that examined nothing is downgraded to `inapplicable` with
        `GATE_DEFECT`, because that is what it is: the gate ran, exited zero,
        and learned nothing. Enforcing it here rather than in each helper is
        the whole point — `bare` has had this check since #81 and chant did
        not, which is #104.
        """
        if self.inapplicable is not None:
            return "inapplicable"
        if not self.checks:
            self.inapplicable = Inapplicable.NO_ARTIFACT
            self.reason = self.reason or "no check ran against this workspace"
            return "inapplicable"
        if any(c.exit_code != 0 for c in self.checks):
            return "fail"
        if self.examined == 0:
            self.inapplicable = Inapplicable.GATE_DEFECT
            self.reason = (
                "every check exited zero while examining nothing — the gate "
                "produced no evidence, so this is not a pass (invariant 1)"
            )
            return "inapplicable"
        return "pass"

    def scoreable(self) -> bool:
        """May this stage enter the correctness denominator?

        Only a real verdict, or an abstention that the task itself declares.
        A GATE_DEFECT must not be scored in either direction — failing the arm
        punishes it for the harness, and dropping it rewards it (#110).
        """
        v = self.verdict()
        if v in ("pass", "fail"):
            return True
        return self.inapplicable is Inapplicable.BY_SPEC

    def to_legacy(self) -> dict:
        """The dict shape the runner and bench.score already consume."""
        v = self.verdict()
        logs = "\n".join(
            f"{c.tool} {' '.join(c.argv)}: exit={c.exit_code} examined={c.examined}"
            + (f"\n{c.detail}" if c.detail else "")
            for c in self.checks
        )
        if v == "inapplicable":
            reason = self.reason or (self.inapplicable.value if self.inapplicable else "")
            return {
                "inapplicable": True,
                "inapplicable_reason": self.inapplicable.value if self.inapplicable else None,
                "reason": reason,
                "logs": "\n".join(x for x in (logs, reason) if x),
                "examined": self.examined,
            }
        return {
            "passed": v == "pass",
            "logs": logs or "stage produced no output",
            "examined": self.examined,
            "tools": [
                {"tool": c.tool, "resolved_path": c.resolved_path,
                 "exit": c.exit_code, "examined": c.examined}
                for c in self.checks
            ],
        }


@runtime_checkable
class Gate(Protocol):
    """What an arm must implement to be gated.

    Deliberately small. The invariants live in `StageResult`, so a gate author
    cannot forget them, and a new arm cannot be added while quietly skipping
    the checks the older arms learned the hard way.
    """

    stack: str

    def run(self, workspace: Path) -> StageResult:
        """Gate the model's work in `workspace`."""
        ...

    def fixture_pass(self, tmp: Path) -> Path:
        """Build a MODEL-SHAPED workspace this gate must pass.

        Not the golden. The golden is the input the gate was written against,
        so it proves almost nothing — every gate passes its golden, including
        the ones that passed nothing else (#104). This fixture should look like
        a plausible correct answer: for crossplane, XRD + Composition with no
        XR; for pulumi, a program with no Pulumi.yaml; for knr-ops, patches
        referenced by filename rather than inlined.
        """
        ...

    def fixture_fail(self, tmp: Path) -> Path:
        """Build a workspace this gate must fail, for a reason the model caused."""
        ...


GATES: dict[str, Gate] = {}
"""Registry of migrated gates. `tests/test_gate_contract.py` runs the
conformance suite over everything registered here, so a gate joins the registry
only once it satisfies the invariants."""


def register(gate: Gate) -> Gate:
    GATES[gate.stack] = gate
    return gate
