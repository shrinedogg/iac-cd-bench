"""Semantic grader for krops T2-generate: onboard eu-central-1-workload.

Every artifact is located by apiVersion/kind and by what it references, not
by path (the content-location rule, #3578ef8): the model is free to mirror
the repo layout or restructure it, so each assertion evaluates whatever
document actually satisfies its content criteria, wherever it landed. The
six checks are unchanged in intent from the task: the ResourceSet gained the
new input (and kept the old ones), the region bundle carries the right
cluster-vars and a FluxInstance pointing at workload/eu-central-01, the new
data bucket has the full secure-by-default posture plus adoption, the new
region gets its own cluster Flux Kustomization, and the AWSManagedCluster
exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402


@pytest.fixture(scope="module")
def docs():
    return gl.all_docs()


# ── 1. ResourceSet input ────────────────────────────────────────────────────


def _rset_inputs(docs):
    rsets = gl.find_docs(docs, kind="ResourceSet",
                         api_version_contains="fluxcd.controlplane.io")
    assert rsets, (
        "expected the flux-apps ResourceSet (kind ResourceSet, "
        f"apiVersion fluxcd.controlplane.io). Workspace: {gl.inventory()}"
    )
    return rsets[0][1].get("spec", {}).get("inputs") or []


def test_resource_set_input_added_and_old_inputs_kept(docs):
    """The new input is present and is an addition, not a replacement."""
    inputs = _rset_inputs(docs)
    assert inputs, "ResourceSet has no spec.inputs"
    new = [i for i in inputs
           if isinstance(i, dict) and str(i.get("region", "")) == "eu-central-1"]
    assert new, (
        "expected a new ResourceSet input for region eu-central-1, got: "
        f"{inputs!r}"
    )
    assert all(str(i.get("cluster", "")) == "eu-central-1-workload" for i in new), (
        f"eu-central-1 input must carry cluster eu-central-1-workload, got: {new!r}"
    )
    regions = [str(i.get("region", "")) for i in inputs if isinstance(i, dict)]
    for old in ("eu-north-1", "eu-west-1"):
        assert old in regions, (
            f"existing input {old!r} was removed — the task says do not change "
            f"the existing inputs. inputs now: {regions!r}"
        )


# ── 2. Region bundle: cluster-vars + FluxInstance ───────────────────────────


def test_region_bundle_cluster_vars(docs):
    """A cluster-vars ConfigMap for the new cluster with the three values."""
    candidates = [
        d for _p, d in gl.find_docs(docs, kind="ConfigMap")
        if gl.name_of(d) == "cluster-vars"
        or any(k in (d.get("data") or {}) for k in ("AWS_REGION", "CLUSTER_NAME"))
    ]
    assert candidates, (
        "expected a cluster-vars ConfigMap for the new cluster "
        f"(metadata.name cluster-vars in namespace flux-system). {gl.inventory()}"
    )
    good = []
    for d in candidates:
        data = d.get("data") or {}
        if (str(data.get("AWS_REGION", "")) == "eu-central-1"
                and str(data.get("CLUSTER_NAME", "")) == "eu-central-1-workload"
                and str(data.get("AWS_ACCOUNT_ID", "")) == "120392301094"):
            good.append(d)
    assert good, (
        "expected a cluster-vars ConfigMap with AWS_REGION eu-central-1, "
        "CLUSTER_NAME eu-central-1-workload and AWS_ACCOUNT_ID 120392301094. "
        f"ConfigMaps found: {[(gl.name_of(d), (d.get('data') or {})) for d in candidates]!r}"
    )


def test_flux_instance_sync_path(docs):
    """The new FluxInstance points Flux at workload/eu-central-01."""
    instances = gl.find_docs(docs, kind="FluxInstance")
    assert instances, (
        "expected a FluxInstance for the new cluster "
        f"(kind FluxInstance, fluxcd.controlplane.io). {gl.inventory()}"
    )
    good = [
        d for _p, d in instances
        if str(gl.deep_get(d, "spec", "sync", "path", default="")) == "workload/eu-central-01"
    ]
    assert good, (
        "expected a FluxInstance with spec.sync.path workload/eu-central-01. "
        f"FluxInstance sync paths found: "
        f"{[gl.deep_get(d, 'spec', 'sync', 'path') for _p, d in instances]!r}"
    )
    assert all(
        str(gl.deep_get(d, "spec", "distribution", "version", default="")) in ("2.x", "2", "v2.x")
        for d in good
    ), "FluxInstance should install Flux distribution 2.x like the existing bundles"


# ── 3. New data bucket, full posture ────────────────────────────────────────


def _new_bucket(docs):
    buckets = gl.find_docs(docs, kind="Bucket", api_version_contains="s3.")
    new = [d for _p, d in buckets if "eu-central-1" in str(d.get("spec", {}).get("name", ""))
           or "eu-central-1" in gl.name_of(d)]
    assert new, (
        "expected a new s3.services.k8s.aws Bucket for the eu-central-1 cluster "
        f"(spec.name krops-120392301094-eu-central-1-workload-data or similar). "
        f"Bucket names found: {[str(d.get('spec', {}).get('name', '')) for _p, d in buckets]!r}"
    )
    return new[0]


def test_new_bucket_security_posture(docs):
    """Versioning + AES256 + all four publicAccessBlock flags on the new bucket."""
    d = _new_bucket(docs)
    versioning = gl.values_under(d, "versioning", "status")
    assert any(gl.truthy(v) and str(v).lower() == "enabled" for v in versioning) or \
        any(str(v).lower() == "enabled" for v in versioning), (
            f"expected versioning Enabled on the new bucket, got: {versioning!r}"
        )
    algos = [str(v).upper() for v in gl.walk_values(d, "sseAlgorithm")]
    assert any(a == "AES256" for a in algos), (
        f"expected AES256 server-side encryption, found: {algos!r}"
    )
    flags = {}
    for key in ("blockPublicACLs", "blockPublicPolicy",
                "ignorePublicACLs", "restrictPublicBuckets"):
        vals = gl.walk_values(d, key)
        if vals:
            flags[key] = vals
    assert len(flags) == 4, (
        "expected all four publicAccessBlock flags set on the new bucket, "
        f"found: {sorted(flags)}"
    )
    not_blocked = {k: v for k, v in flags.items() if not all(gl.truthy(x) for x in v)}
    assert not not_blocked, (
        f"public access is not actually blocked: {not_blocked!r} — every flag must be true"
    )


def test_new_bucket_adoption_and_region(docs):
    """adopt-or-create (double-apply bootstrap) and the region annotation."""
    d = _new_bucket(docs)
    anns = (d.get("metadata") or {}).get("annotations") or {}
    assert str(anns.get("services.k8s.aws/adoption-policy", "")) == "adopt-or-create", (
        "the new bucket must carry services.k8s.aws/adoption-policy: "
        f"adopt-or-create like the existing buckets. annotations: {anns!r}"
    )
    assert str(anns.get("services.k8s.aws/region", "")) == "eu-central-1", (
        "the new bucket is outside the controllers' default region (eu-north-1) "
        f"and must carry services.k8s.aws/region: eu-central-1. annotations: {anns!r}"
    )


# ── 4. Cluster wiring ───────────────────────────────────────────────────────


def test_flux_kustomization_for_new_cluster(docs):
    """A Flux Kustomization reconciles the new cluster's directory."""
    flux = gl.find_docs(docs, kind="Kustomization",
                        api_version_contains="kustomize.toolkit.fluxcd.io")
    naming = [d for _p, d in flux if "clusters/eu-central-1" in gl.doc_text(d)]
    assert naming, (
        "expected a Flux Kustomization (kustomize.toolkit.fluxcd.io) whose path "
        "covers mgmt/aws/clusters/eu-central-1. Flux Kustomization paths found: "
        f"{[gl.deep_get(d, 'spec', 'path') for _p, d in flux]!r}"
    )


def test_aws_managed_cluster_added(docs):
    """The AWSManagedCluster for the new cluster exists at its final name."""
    mc = gl.find_docs(docs, kind="AWSManagedCluster")
    assert any(gl.name_of(d) == "eu-central-1-workload" for _p, d in mc), (
        "expected an AWSManagedCluster named eu-central-1-workload "
        "(in infrastructure/aws-clusters-resources/managedclusters.yaml). "
        f"AWSManagedClusters found: {[gl.name_of(d) for _p, d in mc]!r}"
    )
