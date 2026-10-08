"""Pulumi TypeScript T2-generate: verify ComponentResource structure.

Located by content rather than by path (issue #72). The prompt asks for
Pulumi components; it never mandates `index.ts` or `components/bucket.ts`,
and the previous grader both required that exact tree and called `open()`
on two of those paths -- so a differently-organised answer errored out
rather than failing. The six checks keep their intent.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402


@pytest.fixture(scope="module")
def ts_files() -> list[Path]:
    # node_modules is excluded by _grader_lib (issue #58): a bootstrapped
    # workspace symlinks in whole packages' .ts/.d.ts sources.
    return [p for p in gl.iter_files(".ts") if not p.name.endswith(".d.ts")]


@pytest.fixture(scope="module")
def all_ts(ts_files) -> str:
    return "\n---\n".join(gl.read_text(p) for p in ts_files)


def _files_defining(ts_files, pattern: str) -> list[Path]:
    rx = re.compile(pattern)
    return [p for p in ts_files if rx.search(gl.read_text(p))]


def test_index_exists(all_ts):
    """A Pulumi program entrypoint should exist."""
    assert re.search(r"""import .*from ["']@pulumi/""", all_ts), (
        f"expected a Pulumi program (a .ts file importing @pulumi/*). "
        f"Workspace contains: {gl.inventory()}"
    )


def test_components_dir(all_ts):
    """Resources should be factored into ComponentResource subclasses."""
    assert re.search(r"class\s+\w+\s+extends\s+[\w.]*ComponentResource", all_ts), (
        "expected at least one pulumi.ComponentResource subclass -- the task "
        "asks for components, not a flat program"
    )


def test_bucket_component(ts_files):
    """An S3 bucket component should be created."""
    hits = _files_defining(ts_files, r"s3\.Bucket")
    assert hits, (
        f"expected a component declaring an S3 bucket (aws.s3.Bucket). "
        f"TypeScript files present: {[str(p) for p in ts_files]!r}"
    )


def test_rds_component(ts_files):
    """An RDS component should be created."""
    hits = _files_defining(ts_files, r"rds\.Instance|rds\.Cluster")
    assert hits, (
        f"expected a component declaring an RDS instance (aws.rds.Instance). "
        f"TypeScript files present: {[str(p) for p in ts_files]!r}"
    )


def test_bucket_versioning(ts_files):
    """The bucket component should enable versioning."""
    hits = _files_defining(ts_files, r"s3\.Bucket")
    assert hits, "no S3 bucket component found to check versioning on"
    assert any("versioning" in gl.read_text(p).lower() for p in hits), \
        "the S3 bucket component should configure versioning"


def test_rds_deletion_protection(ts_files):
    """The RDS component should support deletion protection."""
    hits = _files_defining(ts_files, r"rds\.Instance|rds\.Cluster")
    assert hits, "no RDS component found to check deletion protection on"
    assert any("deletionprotection" in gl.read_text(p).lower() for p in hits), \
        "the RDS component should support deletionProtection"
