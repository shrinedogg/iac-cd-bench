"""Semantic grader for knr-ops T2-generate: ACK logs bucket + IRSA + Flux.

Locates every artifact by apiVersion/kind and by what it references, not by
path. The prompt asks for a logs bucket, an IRSA role and a separate Flux
kustomization; it never states a file layout, and the previous version of
this grader read `infra/s3/logs-bucket.yaml` and `flux/kustomizations.yaml`
directly -- so an answer that wrote `infra/s3/logs/bucket.yaml` and
`flux/logs-bucket-kustomization.yaml` (kustomize built it, yq parsed it)
scored 0/6, with all six assertions erroring out on one FileNotFoundError.
See issue #72.

The six checks are unchanged in intent: the bucket exists, has versioning,
has AES256 encryption, blocks public access, has an IAM role for IRSA, and
is reconciled by a Flux Kustomization. What changed is that each is now
evaluated against the ACK documents that actually reference the logs
bucket, wherever the model put them, and each fails on its own.

The "references the logs bucket" test is deliberately not a workspace-wide
grep: a document counts only if `logs` appears in its own metadata.name,
its spec's bucket/bucketRef, or (for the IAM and Flux objects) its own
body. A correct answer for the *assets* bucket that never mentions logs
still fails every one of these.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402


def _refs_logs(doc: dict) -> bool:
    """Whether an S3 document is about the logs bucket.

    Both idioms in play: the resource may carry the real bucket name
    (`myapp-logs-dev`) directly, or -- as kustomize overlays encourage -- be
    named `logs-bucket` in the base with the env-specific name patched in.
    """
    haystacks = [gl.name_of(doc)]
    spec = doc.get("spec")
    if isinstance(spec, dict):
        haystacks += [str(v) for v in gl.walk_values(spec, "bucket")]
        haystacks += [str(v) for v in gl.walk_values(spec, "bucketRef")]
        haystacks += [str(v) for v in gl.walk_values(spec, "bucketName")]
    return any("logs" in h.lower() for h in haystacks)


@pytest.fixture(scope="module")
def docs():
    return gl.all_docs()


@pytest.fixture(scope="module")
def s3_logs_docs(docs):
    """Every ACK/upbound S3 document that concerns the logs bucket."""
    return [
        (p, d) for p, d in gl.find_docs(docs, api_version_contains="s3.")
        if _refs_logs(d)
    ]


def test_bucket_manifest_exists(s3_logs_docs):
    """An S3 Bucket resource for the logs bucket, wherever it was written."""
    buckets = [d for _p, d in s3_logs_docs if d.get("kind") == "Bucket"]
    assert buckets, (
        "expected an ACK/upbound S3 Bucket resource for the logs bucket "
        f"(kind: Bucket, apiVersion s3.*). Workspace contains: {gl.inventory()}"
    )


def test_bucket_versioning(s3_logs_docs):
    """Versioning enabled -- on the Bucket itself or on a companion
    BucketVersioning resource, in either the mapping or list CRD dialect."""
    assert s3_logs_docs, (
        f"no S3 resources referencing the logs bucket. Workspace: {gl.inventory()}"
    )
    statuses = []
    for _p, d in s3_logs_docs:
        statuses += [str(v) for v in gl.values_under(d, "versioningConfiguration", "status")]
        statuses += [str(v) for v in gl.values_under(d, "versioning", "status")]
        statuses += [str(v) for v in gl.values_under(d, "versioning", "enabled")]
    assert any(gl.truthy(s) for s in statuses), (
        f"expected versioning Enabled on the logs bucket, found: {statuses!r}"
    )


def test_bucket_encryption(s3_logs_docs):
    """AES256 server-side encryption on the logs bucket."""
    assert s3_logs_docs, (
        f"no S3 resources referencing the logs bucket. Workspace: {gl.inventory()}"
    )
    algorithms = []
    for _p, d in s3_logs_docs:
        algorithms += [str(v) for v in gl.walk_values(d, "sseAlgorithm")]
        algorithms += [str(v) for v in gl.walk_values(d, "sSEAlgorithm")]
        algorithms += [str(v) for v in gl.walk_values(d, "SSEAlgorithm")]
    assert any(a.upper() == "AES256" for a in algorithms), (
        f"expected AES256 server-side encryption on the logs bucket, found: {algorithms!r}"
    )


def test_public_access_blocked(s3_logs_docs):
    """Public access blocked -- and blocked, not merely mentioned: any
    blockPublic*/ignorePublicAcls/restrictPublicBuckets flag the answer sets
    must be true."""
    assert s3_logs_docs, (
        f"no S3 resources referencing the logs bucket. Workspace: {gl.inventory()}"
    )
    flags: dict[str, list] = {}
    for _p, d in s3_logs_docs:
        for key in ("blockPublicAcls", "blockPublicPolicy",
                    "ignorePublicAcls", "restrictPublicBuckets"):
            values = gl.walk_values(d, key)
            if values:
                flags.setdefault(key, []).extend(values)
    assert flags, (
        "expected a public access block for the logs bucket "
        "(a BucketPublicAccessBlock resource, or blockPublic* fields on the bucket)"
    )
    not_blocked = {k: v for k, v in flags.items() if not all(gl.truthy(x) for x in v)}
    assert not not_blocked, (
        f"public access is not actually blocked: {not_blocked!r} -- every "
        f"blockPublic*/ignorePublicAcls/restrictPublicBuckets flag must be true"
    )


def test_iam_role_created(docs):
    """An IRSA role for the logs bucket: an ACK/upbound IAM Role that names
    logs, or whose trust/permission policy grants on a logs bucket ARN."""
    iam_docs = gl.find_docs(docs, api_version_contains="iam.")
    roles = [
        d for _p, d in iam_docs
        if d.get("kind") in ("Role", "RolePolicy", "RolePolicyAttachment", "Policy")
        and "logs" in gl.doc_text(d).lower()
    ]
    assert any(d.get("kind") == "Role" for d in roles), (
        "expected an IAM Role for IRSA scoped to the logs bucket "
        f"(kind: Role, apiVersion iam.*). IAM kinds found: "
        f"{sorted({d.get('kind') for _p, d in iam_docs})!r}"
    )


def test_flux_kustomization_added(docs):
    """A Flux Kustomization that reconciles the logs bucket -- a new one, or
    the existing set extended, either way it must name logs."""
    flux = gl.find_docs(docs, kind="Kustomization",
                        api_version_contains="kustomize.toolkit.fluxcd.io")
    naming_logs = [d for _p, d in flux if "logs" in gl.doc_text(d).lower()]
    assert naming_logs, (
        "expected a Flux Kustomization (kustomize.toolkit.fluxcd.io) referencing "
        f"the logs bucket. Flux Kustomizations found: "
        f"{[gl.name_of(d) for _p, d in flux]!r}"
    )
