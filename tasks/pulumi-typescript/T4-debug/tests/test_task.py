"""Pulumi TypeScript T4-debug: verify defect fixes.

Locates the corrected program by content rather than opening `index.ts`
(issue #72): a model that writes its fix under any other name previously
errored both assertions out on FileNotFoundError. The answer is the .ts
file that no longer carries the seed's defect marker (`const resolved =
await arn`); if every candidate still carries it, the seeded text is graded
as-is, so an unchanged answer fails on the defect rather than on a missing
file.

The two assertions themselves are unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402

_DEFECT = "await arn"


@pytest.fixture(scope="module")
def content() -> str:
    return gl.answer_text(
        "the corrected Pulumi program", ".ts",
        defect_marker=_DEFECT,
        fallback_block_marker="@pulumi/",
    )


def test_no_async_in_index(content):
    """Should not have async functions that return Promises for Outputs"""
    # After fix, should use .apply() instead of async/await for Outputs
    assert "async function" not in content or "pulumi.output" not in content, \
        "Should not mix async functions with pulumi.output() wrapping"


def test_proper_output_handling(content):
    """Should use .apply() for Output transformations"""
    # Should have .apply() for proper Output handling
    assert ".apply(" in content or "Output." in content, \
        "Should use .apply() or Output operations for transformations"
