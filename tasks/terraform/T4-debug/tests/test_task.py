"""Terraform T4-debug: verify the seeded defects are actually fixed (#75).

Locates the HCL by content rather than opening `main.tf` (#72): a model that
split its corrected configuration across files, or named it anything else,
previously errored every assertion out on FileNotFoundError.

These assertions used to be vacuous. Two of them matched strings the seed
already contained (`aws_db_instance`, `deletion_protection`) and the third
ended in a literal `assert True`, so a model that changed nothing scored 3/3
on the debug archetype. Each one now asserts the defect is ABSENT and the
correction PRESENT, which is the only form that can distinguish a fix from a
no-op.

The seed is the negative fixture: `tests/test_grader_seeds.py` runs this file
against `../seed` and requires it to fail. Tightening these moves historical
terraform T4 numbers, which is the point — rescore with
tools/regrade_offline.py rather than re-running.

Seeded defects, from seed/main.tf:
  1. `deletion_protection = false` on the prod database
  2. `count = length(var.envs)` where the resource should key by environment
  3. `output.db_endpoint` reaching into `aws_db_instance.main[0]`
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402

TFVARS_SUFFIXES = (".tfvars",)


@pytest.fixture(scope="module")
def tf_text() -> str:
    return gl.require_text("the corrected Terraform configuration", ".tf")


@pytest.fixture(scope="module")
def tfvars_text() -> str:
    return "\n".join(gl.read_text(p) for p in gl.iter_files(*TFVARS_SUFFIXES))


def test_deletion_protection_is_enabled(tf_text, tfvars_text):
    """Defect 1: the seed ships `deletion_protection = false`.

    Mentioning the attribute is not a fix — the seed mentions it. It has to
    stop being false.
    """
    both = f"{tf_text}\n{tfvars_text}"

    assert re.search(r"deletion_protection\s*=\s*false", both, re.I) is None, (
        "`deletion_protection = false` is still present; the prod database is "
        "still destroyable"
    )
    assert re.search(r"deletion_protection\s*=", both, re.I), (
        "deletion_protection is not set at all"
    )


def test_resource_keys_by_environment_instead_of_count(tf_text):
    """Defect 2: `count = length(var.envs)` on a resource that should key by
    environment. `for_each` is the correction; count is the defect."""
    assert re.search(r"^\s*count\s*=\s*length\(", tf_text, re.M) is None, (
        "`count = length(...)` is still present — the count/for_each type "
        "mismatch is unfixed"
    )
    assert "for_each" in tf_text, (
        "no for_each anywhere; the resource still cannot key by environment"
    )


def test_output_does_not_index_a_counted_resource(tf_text):
    """Defect 3: `aws_db_instance.main[0].endpoint`. Indexing position 0 is
    what ties the output to `count` and produces the dependency error."""
    assert re.search(r"aws_db_instance\.\w+\[0\]", tf_text) is None, (
        "output still indexes the database at [0]; that is the counted-resource "
        "reference the task asks to remove"
    )
    assert "aws_db_instance" in tf_text, (
        "the database resource is gone entirely — that is not a fix"
    )
