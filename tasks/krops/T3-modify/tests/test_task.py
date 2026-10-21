"""Semantic grader for krops T3-modify: production-grade eu-west-1 only.

krops has no base/overlay pair: clusters are separated by directory and by
per-cluster docs in shared multi-doc files. So the "don't touch dev" check
here is a no-leak check across docs and files: the eu-west-1 doc/file must
carry the new values, and anything that identifies as eu-north-1 (the other
DBInstance doc in the same file, an eu-north-1 cluster file, the eu-north-1
cluster-vars) must keep its old values.

Files are located by content (apiVersion/kind + region markers), never by
path: the prompt names repo paths for realism, but the model is allowed to
restructure. Region identity comes from the docs themselves — AZ names on
the AWSManagedMachinePool, spec.region on the control plane, or
data.AWS_REGION on cluster-vars.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402


@pytest.fixture(scope="module")
def docs():
    return gl.all_docs()


@pytest.fixture(scope="module")
def by_path(docs):
    out: dict[Path, list[dict]] = defaultdict(list)
    for p, d in docs:
        out[p].append(d)
    return out


def _dbinstances(docs):
    return gl.find_docs(docs, kind="DBInstance", api_version_contains="rds.")


def _west1_dbinstance(docs):
    hits = [d for _p, d in _dbinstances(docs)
            if "eu-west-1" in gl.name_of(d) or "eu-west-1" in str(gl.deep_get(d, "spec", "dbInstanceIdentifier", default=""))]
    return hits[0] if hits else None


def _north1_dbinstance(docs):
    hits = [d for _p, d in _dbinstances(docs)
            if "eu-north-1" in gl.name_of(d) or "eu-north-1" in str(gl.deep_get(d, "spec", "dbInstanceIdentifier", default=""))]
    return hits[0] if hits else None


def test_eu_west1_rds_class_upgraded(docs):
    d = _west1_dbinstance(docs)
    assert d is not None, (
        "expected the eu-west-1 DBInstance doc (dbInstanceIdentifier "
        f"krops-eu-west-1-workload-db) in the returned file. {gl.inventory()}"
    )
    cls = str(gl.deep_get(d, "spec", "dbInstanceClass", default=""))
    assert cls.lower() == "db.m6g.large", f"expected dbInstanceClass db.m6g.large, got: {cls!r}"


def test_eu_west1_rds_multi_az_enabled(docs):
    d = _west1_dbinstance(docs)
    assert d is not None, "expected the eu-west-1 DBInstance doc"
    multi = gl.deep_get(d, "spec", "multiAZ", default=None)
    assert gl.truthy(multi), f"expected multiAZ: true on the eu-west-1 DBInstance, got: {multi!r}"


def _arm_pool_for_region(by_path, region: str) -> tuple[list[dict], list[dict]]:
    """(MachinePool workload-arm docs, AWSManagedMachinePool workload-arm docs)
    from files whose arm pool belongs to `region` — the AZ list (or, failing
    that, the control-plane spec.region / Cluster label in the same file) is
    the region marker."""
    pools, infras = [], []
    for p, file_docs in by_path.items():
        arm_infras = [d for d in file_docs
                      if d.get("kind") == "AWSManagedMachinePool" and gl.name_of(d) == "workload-arm"]
        if not arm_infras:
            continue
        azs = " ".join(str(v) for v in gl.walk_values(arm_infras[0], "availabilityZones"))
        cp_regions = [str(gl.deep_get(d, "spec", "region", default=""))
                      for d in file_docs if d.get("kind") == "AWSManagedControlPlane"]
        labels = " ".join(str(v) for v in gl.walk_values(file_docs, "region"))
        if region in azs or region in cp_regions or region in labels:
            pools += [d for d in file_docs
                      if d.get("kind") == "MachinePool" and gl.name_of(d) == "workload-arm"]
            infras += arm_infras
    return pools, infras


def test_eu_west1_arm_pool_scaled(by_path):
    pools, infras = _arm_pool_for_region(by_path, "eu-west-1")
    assert pools, (
        "expected the eu-west-1 workload-arm MachinePool in the returned "
        "cluster file (identifiable by its eu-west-1a/b/c AZs)."
    )
    replicas = [gl.deep_get(d, "spec", "replicas") for d in pools]
    assert any(str(r) == "5" for r in replicas), (
        f"expected workload-arm replicas 5 on eu-west-1, got: {replicas!r}"
    )
    if infras:
        maxes = [gl.deep_get(d, "spec", "scaling", "maxSize") for d in infras]
        assert any(str(m) == "8" for m in maxes), (
            f"expected scaling.maxSize 8 on the eu-west-1 arm pool, got: {maxes!r}"
        )


def test_eu_west1_cluster_vars_extended(docs):
    cms = gl.find_docs(docs, kind="ConfigMap", name="cluster-vars")
    if not cms:
        # fall back to any ConfigMap carrying the cluster-vars data keys
        cms = [d for _p, d in gl.find_docs(docs, kind="ConfigMap")
               if "AWS_REGION" in (d.get("data") or {})]
    assert cms, "expected the eu-west-1 cluster-vars ConfigMap in the output"
    west = [d for _p, d in cms if str((d.get("data") or {}).get("AWS_REGION", "")) == "eu-west-1"]
    assert west, (
        "expected a cluster-vars ConfigMap with AWS_REGION eu-west-1. "
        f"found: {[(d.get('data') or {}).get('AWS_REGION') for _p, d in cms]!r}"
    )
    data = west[0].get("data") or {}
    assert str(data.get("TELEMETRY_ENABLED", "")) == "true", (
        f"expected TELEMETRY_ENABLED: \"true\" added to the eu-west-1 cluster-vars, "
        f"got data: {data!r}"
    )
    for key, val in (("AWS_REGION", "eu-west-1"), ("CLUSTER_NAME", "eu-west-1-workload"),
                     ("AWS_ACCOUNT_ID", "120392301094")):
        assert str(data.get(key, "")) == val, (
            f"existing cluster-vars key {key} must be preserved, got: {data.get(key)!r}"
        )


def test_eu_north1_untouched(docs, by_path):
    """The no-leak half: whatever identifies as eu-north-1 keeps its old values."""
    north = _north1_dbinstance(docs)
    if north is not None:
        cls = str(gl.deep_get(north, "spec", "dbInstanceClass", default=""))
        assert cls.lower() == "db.t4g.micro", (
            f"eu-north-1 DBInstance class must stay db.t4g.micro, got: {cls!r} — "
            "the change is scoped to eu-west-1"
        )
        multi = gl.deep_get(north, "spec", "multiAZ", default=None)
        assert not gl.truthy(multi), (
            f"eu-north-1 DBInstance multiAZ must stay false, got: {multi!r}"
        )
    north_pools, north_infras = _arm_pool_for_region(by_path, "eu-north-1")
    if north_pools:
        replicas = [str(gl.deep_get(d, "spec", "replicas")) for d in north_pools]
        assert all(r == "3" for r in replicas), (
            f"eu-north-1 workload-arm replicas must stay 3, got: {replicas!r}"
        )
    if north_infras:
        maxes = [str(gl.deep_get(d, "spec", "scaling", "maxSize")) for d in north_infras]
        assert all(m == "5" for m in maxes), (
            f"eu-north-1 arm scaling.maxSize must stay 5, got: {maxes!r}"
        )
    north_vars = [d for _p, d in gl.find_docs(docs, kind="ConfigMap", name="cluster-vars")
                  if str((d.get("data") or {}).get("AWS_REGION", "")) == "eu-north-1"]
    for d in north_vars:
        assert "TELEMETRY_ENABLED" not in (d.get("data") or {}), (
            "the TELEMETRY_ENABLED key is eu-west-1 only — it leaked into eu-north-1"
        )
