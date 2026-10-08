"""Pulumi Python T2-generate: verify ComponentResource structure.

Located by content rather than by path (issue #72). The prompt asks for
Pulumi components; it never mandates `__main__.py` or `components/bucket.py`,
and the previous grader both required that exact tree and called `open()` on
two of those paths -- so a differently-organised answer errored out rather
than failing. The six checks keep their intent: an entrypoint exists,
resources are factored into ComponentResource classes, a bucket component
and an RDS component exist, and each carries its required property.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402


@pytest.fixture(scope="module")
def py_files() -> list[Path]:
    return gl.iter_files(".py")


@pytest.fixture(scope="module")
def all_py(py_files) -> str:
    return "\n---\n".join(gl.read_text(p) for p in py_files)


def _files_defining(py_files, pattern: str) -> list[Path]:
    rx = re.compile(pattern)
    return [p for p in py_files if rx.search(gl.read_text(p))]


def test_main_exists(all_py):
    """A Pulumi program entrypoint should exist."""
    assert re.search(r"^\s*import\s+pulumi|^\s*from\s+pulumi", all_py, re.MULTILINE), (
        f"expected a Pulumi program (a .py file importing pulumi). "
        f"Workspace contains: {gl.inventory()}"
    )


def test_components_dir(all_py):
    """Resources should be factored into ComponentResource subclasses."""
    assert re.search(r"class\s+\w+\s*\(\s*[\w.]*ComponentResource\s*\)", all_py), (
        "expected at least one pulumi.ComponentResource subclass -- the task "
        "asks for components, not a flat program"
    )


def test_bucket_component(py_files):
    """An S3 bucket component should be created."""
    hits = _files_defining(py_files, r"s3\.Bucket|s3\.BucketV2")
    assert hits, (
        f"expected a component declaring an S3 bucket (aws.s3.Bucket). "
        f"Python files present: {[str(p) for p in py_files]!r}"
    )


def test_rds_component(py_files):
    """An RDS component should be created."""
    hits = _files_defining(py_files, r"rds\.Instance|rds\.Cluster")
    assert hits, (
        f"expected a component declaring an RDS instance (aws.rds.Instance). "
        f"Python files present: {[str(p) for p in py_files]!r}"
    )


def test_bucket_versioning(py_files):
    """The bucket component should enable versioning."""
    hits = _files_defining(py_files, r"s3\.Bucket|s3\.BucketV2")
    assert hits, "no S3 bucket component found to check versioning on"
    assert any("versioning" in gl.read_text(p).lower() for p in hits), \
        "the S3 bucket component should configure versioning"


def test_rds_deletion_protection(py_files):
    """The RDS component should support deletion protection."""
    hits = _files_defining(py_files, r"rds\.Instance|rds\.Cluster")
    assert hits, "no RDS component found to check deletion protection on"
    assert any("deletion_protection" in gl.read_text(p).lower()
               or "deletionprotection" in gl.read_text(p).lower() for p in hits), \
        "the RDS component should support deletion protection"
