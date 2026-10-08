"""Semantic grader for terraform T3-modify: refactor dev/prod into one module.

Written to close #111. terraform, crossplane and both pulumi arms shipped no
T3-modify grader, so their semantic stage recorded `inapplicable` on this task
while chant, knr-ops and bare were graded on it. Under rule 10 an abstention
leaves the correctness denominator, so those four arms were scored over 2.25
attempted stages against 2.50 for the arms that had a grader (#110). The
composites were not the same measurement, and crossplane outranked knr-ops
largely on that.

Like knr-ops T3, this task ships no seed: the model writes the refactor from
the prompt's description rather than editing a fixture, so there is nothing to
diff against. The grader reads the emitted HCL instead.

The prompt asks for three things, and each is asserted separately so partial
credit is real rather than all-or-nothing:

  1. one module, invoked with for_each, replacing the duplicated blocks
  2. the per-environment duplication actually gone
  3. `moved` blocks, because the prompt requires the state move be zero-diff --
     a refactor without them is a destroy/create, which is the opposite of what
     was asked

HCL is read as text, not parsed: terraform's own parser is not importable here
and hcl2 is not in the toolchain. The patterns are deliberately loose about
whitespace and quoting so a correct answer is not failed for formatting -- the
#102/#107 lesson, where graders recognised exactly one spelling of a right
answer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _all_hcl() -> str:
    """Every .tf the model emitted, plus the raw completion as a fallback.

    A model that describes its answer in prose with fenced HCL, without ever
    naming a path, still gets read -- the extractor may not have written a .tf
    at all, and failing that answer would be grading the extractor (#108).
    """
    chunks = []
    for p in sorted(Path(".").rglob("*.tf")):
        try:
            chunks.append(p.read_text())
        except OSError:
            continue
    if not chunks:
        out = Path("model_output.md")
        if out.is_file():
            try:
                chunks.append(out.read_text())
            except OSError:
                pass
    return "\n".join(chunks)


@pytest.fixture(scope="module")
def hcl() -> str:
    return _all_hcl()


def test_answer_is_present(hcl):
    assert hcl.strip(), (
        "no .tf content and no model_output.md — nothing to grade"
    )


def test_a_module_is_declared_or_invoked(hcl):
    """The refactor's whole point: one module in place of duplicated blocks."""
    assert re.search(r'\bmodule\s+"[^"]+"\s*\{', hcl), (
        "expected a `module \"...\" {` block — the prompt asks for the "
        "duplicated dev/prod blocks to be refactored into one module"
    )


def test_module_is_driven_by_for_each(hcl):
    """`for_each` specifically, not `count`: the prompt names it, and count
    keys state by index, which makes the zero-diff move impossible."""
    m = re.search(r'\bmodule\s+"[^"]+"\s*\{(.*?)\n\}', hcl, re.S)
    block = m.group(1) if m else hcl
    assert re.search(r'\bfor_each\s*=', block), (
        "expected `for_each` on the module invocation. `count` is not "
        "equivalent here: it keys state by index, so adding or reordering an "
        "environment renumbers every resource and the move cannot be zero-diff"
    )


def test_environments_are_data_not_duplicated_blocks(hcl):
    """dev and prod should appear as keys the module iterates, not as two
    hand-written copies of the same resources."""
    assert re.search(r'"?dev"?\s*[:=]', hcl) and re.search(r'"?prod"?\s*[:=]', hcl), (
        "expected dev and prod to appear as for_each keys/values driving one "
        "module, rather than as duplicated resource blocks"
    )
    # The duplication the task exists to remove: the same resource type
    # declared once per environment with an env-suffixed name.
    dup = re.findall(r'resource\s+"(\w+)"\s+"(\w*(?:dev|prod)\w*)"', hcl)
    by_type: dict[str, set[str]] = {}
    for rtype, name in dup:
        by_type.setdefault(rtype, set()).add(name)
    duplicated = {t: n for t, n in by_type.items() if len(n) > 1}
    assert not duplicated, (
        f"these resource types are still declared once per environment: "
        f"{duplicated!r} — that is the duplication the refactor removes"
    )


def test_state_move_is_declared(hcl):
    """`moved` blocks, or an equivalent, because the prompt requires zero-diff.

    Refactoring resources into a module changes their addresses. Without a
    declared move terraform plans a destroy and a create for every one of
    them, which is the opposite of the requirement — and it is invisible in
    the HCL itself, which is exactly why it needs asserting.
    """
    assert re.search(r'\bmoved\s*\{', hcl) or re.search(r'terraform\s+state\s+mv', hcl), (
        "expected `moved { from = ... to = ... }` blocks (or a documented "
        "`terraform state mv`). The prompt requires the state move be "
        "zero-diff; without a declared move, refactoring into a module "
        "re-addresses every resource and plans destroy/create"
    )
