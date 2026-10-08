"""Semantic grader for bare T3-modify: scale workers in both dev and prod.

bare has no shared base/overlay, so the fix must land in BOTH the dev and
the prod worker manifests independently -- this grader asserts each
environment separately rather than accepting a change in only one.

Environment scoping comes from the resource names (`myapp-dev-workers` /
`myapp-prod-workers`), not from the file the model wrote them to: the
objects are located by kind+name anywhere in the workspace rather than in
`dev/workers.yaml` and `prod/workers.yaml` specifically (issue #72).

The scale-up is satisfied by any matching MachineDeployment carrying the
new replica count; the "unchanged" invariants require *every* matching
AWSMachineTemplate to still carry the original instance type, so a rewrite
that leaves a corrected copy beside a mangled one cannot pass on the
corrected copy alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402


@pytest.fixture(scope="module")
def all_docs():
    return gl.all_docs()


def _find(kind: str, name: str, docs) -> list[dict]:
    return [d for _p, d in gl.find_docs(docs, kind=kind, name=name)]


def test_dev_replicas_scaled(all_docs):
    mds = _find("MachineDeployment", "myapp-dev-workers", all_docs)
    assert mds, ("myapp-dev-workers MachineDeployment not found anywhere in the "
                 f"workspace. Workspace contains: {gl.inventory()}")
    replicas = [gl.deep_get(d, "spec", "replicas") for d in mds]
    assert any(r == 3 for r in replicas), \
        f"dev replicas: expected 3, got {replicas!r}"


def test_dev_instance_type_unchanged(all_docs):
    tmpls = _find("AWSMachineTemplate", "myapp-dev-workers", all_docs)
    assert tmpls, ("myapp-dev-workers AWSMachineTemplate not found anywhere in the "
                   f"workspace. Workspace contains: {gl.inventory()}")
    types = [gl.deep_get(d, "spec", "template", "spec", "instanceType") for d in tmpls]
    assert all(t == "t3.medium" for t in types), \
        f"dev instanceType should stay t3.medium, got {types!r}"


def test_prod_replicas_scaled(all_docs):
    mds = _find("MachineDeployment", "myapp-prod-workers", all_docs)
    assert mds, ("myapp-prod-workers MachineDeployment not found anywhere in the "
                 f"workspace. Workspace contains: {gl.inventory()}")
    replicas = [gl.deep_get(d, "spec", "replicas") for d in mds]
    assert any(r == 6 for r in replicas), \
        f"prod replicas: expected 6, got {replicas!r}"


def test_prod_instance_type_unchanged(all_docs):
    tmpls = _find("AWSMachineTemplate", "myapp-prod-workers", all_docs)
    assert tmpls, ("myapp-prod-workers AWSMachineTemplate not found anywhere in the "
                   f"workspace. Workspace contains: {gl.inventory()}")
    types = [gl.deep_get(d, "spec", "template", "spec", "instanceType") for d in tmpls]
    assert all(t == "t3.large" for t in types), \
        f"prod instanceType should stay t3.large, got {types!r}"
