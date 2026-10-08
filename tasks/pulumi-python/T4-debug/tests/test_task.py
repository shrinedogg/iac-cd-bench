"""Pulumi Python T4-debug: verify defect fixes.

Locates the corrected program by content rather than opening `__main__.py`
(issue #72): a model that writes its fix under any other name previously
errored both assertions out on FileNotFoundError. The answer is the .py
file that no longer carries the seed's defect marker
(`config.get("dbPassword")`); if every candidate still carries it, the
seeded text is graded as-is, so an unchanged answer fails on the defect
rather than on a missing file.

The two assertions themselves are unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402

_DEFECT = 'config.get("dbPassword")'


@pytest.fixture(scope="module")
def content() -> str:
    return gl.answer_text(
        "the corrected Pulumi program", ".py",
        defect_marker=_DEFECT,
        fallback_block_marker="pulumi",
    )


def test_secret_not_plain(content):
    """Password should be read as a Secret, not plain string"""
    assert "require_secret" in content or "Secret" in content, \
        "Password should be read as a Secret using require_secret()"


def test_apply_not_on_output(content):
    """Should not misuse .apply() on Output values"""
    # After fix, should use .apply() properly with a callable
    # or use Output operations directly
    assert content.count(".apply(") <= 2, \
        "Should not overuse .apply() on Output values"
