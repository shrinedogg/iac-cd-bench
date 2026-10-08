"""Crossplane T4-debug: fix the Composition's patch path + readiness checks.

Locates the Composition by kind rather than by opening `composition.yaml`
(issue #72): the seed ships that filename, but a model that writes its
corrected Composition anywhere else -- or under any other name -- was
previously graded as having produced nothing, with both assertions erroring
out on FileNotFoundError instead of failing.

The two checks are unchanged in substance. Both are evaluated against every
Composition in the workspace and pass if any one of them is fixed, which is
what makes a corrected copy written beside the untouched seed count.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402


@pytest.fixture(scope="module")
def composition_texts() -> list[str]:
    """Every Composition in the workspace, re-serialised.

    Falls back to the model's fenced blocks when nothing was extracted --
    a Composition block whose path was never backticked still represents
    the answer.
    """
    docs = gl.find_docs(kind="Composition")
    if docs:
        return [gl.doc_text(d) for _p, d in docs]
    return [code for _lang, code in gl.fenced_blocks(langs=("", "yaml", "yml"))
            if "kind: Composition" in code]


def test_composition_has_correct_field_path(composition_texts):
    """Composition should reference spec.parameters.instanceClass"""
    assert composition_texts, (
        f"no Composition found in the workspace. Workspace contains: {gl.inventory()}"
    )
    assert any("spec.parameters.instanceClass" in t for t in composition_texts), \
        "Should use spec.parameters.instanceClass instead of spec.parameters.storageClass"


def test_composition_has_readiness_checks(composition_texts):
    """Composition should have ReadinessChecks on RDS resource"""
    assert composition_texts, (
        f"no Composition found in the workspace. Workspace contains: {gl.inventory()}"
    )
    # `readinessChecks` is the field's real name on a ComposedTemplate. The
    # previous assertion wanted "ReadinessChecks" (capital R) or "checkType",
    # neither of which is a Crossplane spelling -- so a correct answer written
    # in valid Crossplane could not satisfy it, and only a comment or a
    # misspelling could (#84's shape).
    assert any("readinessChecks" in t for t in composition_texts), (
        "Composition should declare readinessChecks on the composed resource; "
        "without them it never reports Ready"
    )
