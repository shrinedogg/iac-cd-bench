"""Semantic grader for pulumi-typescript T3-modify: migrate a flat program to
a ComponentResource without replacement, using aliases.

Written to close #111 -- see tasks/terraform/T3-modify/tests/test_task.py.

This is the arm whose prompt most explicitly names its own mechanism: "Use
`aliases`". Moving resources under a ComponentResource changes their URNs, and
without an alias Pulumi sees the old URN disappear and a new one appear -- a
destroy and a create. The alias is not a stylistic detail, it is the entire
difference between the requested refactor and a rebuild, and it is invisible
in the resource tree itself. It gets its own assertion, and a second one that
it is not merely present but populated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _source() -> str:
    chunks = []
    for p in sorted(Path(".").rglob("*.ts")):
        if "node_modules" in p.parts:
            continue
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
def src() -> str:
    return _source()


def test_answer_is_present(src):
    assert src.strip(), "no TypeScript and no model_output.md — nothing to grade"


def test_a_component_resource_is_defined(src):
    assert re.search(r"extends\s+(pulumi\.)?ComponentResource", src), (
        "expected a class extending pulumi.ComponentResource — the task asks "
        "for the flat program to be migrated into one"
    )


def test_the_component_registers_itself(src):
    """A ComponentResource must call super with its type token; without it the
    children have no parent URN to be re-parented under."""
    assert re.search(r"super\s*\(", src), (
        "expected the component to call super(...) with its type token"
    )


def test_children_are_parented_to_the_component(src):
    """Re-parenting is what the migration does. Without `parent: this` the
    resources stay top-level and nothing has actually moved."""
    assert re.search(r"parent\s*:\s*this", src), (
        "expected child resources to pass `{ parent: this }` — without it "
        "they remain top-level and the migration has not happened"
    )


def test_aliases_are_used(src):
    """The prompt names this explicitly, and it is the whole no-replacement
    requirement: re-parenting changes every child's URN, and without an alias
    Pulumi reads that as a delete plus a create."""
    assert re.search(r"\baliases\s*:", src), (
        "expected `aliases: [...]` on the migrated resources. Re-parenting "
        "changes their URNs; without an alias Pulumi plans a destroy and a "
        "create, which is precisely what the prompt forbids"
    )


def test_aliases_name_a_previous_identity(src):
    """An empty alias list satisfies a grep and nothing else."""
    m = re.search(r"aliases\s*:\s*\[(.*?)\]", src, re.S)
    assert m and m.group(1).strip(), (
        "`aliases` is present but empty — it must name the resource's previous "
        "identity (e.g. `{ parent: pulumi.rootStackResource }` or an explicit "
        "`urn:`)"
    )
