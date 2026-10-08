"""Semantic coverage grader for bare T2-generate.

Checks the extracted YAML in the model's workspace covers the spec: a new
logs bucket added independently to BOTH dev/ and prod/ (no shared base
means each environment's coverage is graded separately), with versioning,
encryption, a public access block, and a scoped IAM grant.
"""

from pathlib import Path

import pytest


def _all_yaml_text() -> str:
    """Concatenate every YAML file in the workspace (any location the model chose)."""
    ws = Path(".")
    chunks = []
    for p in sorted(ws.rglob("*.yaml")) + sorted(ws.rglob("*.yml")):
        try:
            chunks.append(p.read_text())
        except Exception:
            continue
    return "\n---\n".join(chunks)


def _files_mentioning(needle: str) -> list[Path]:
    ws = Path(".")
    hits = []
    for p in sorted(ws.rglob("*.yaml")) + sorted(ws.rglob("*.yml")):
        try:
            if needle in p.read_text():
                hits.append(p)
        except Exception:
            continue
    return hits


@pytest.fixture(scope="module")
def all_yaml() -> str:
    return _all_yaml_text()


def test_dev_logs_bucket_exists(all_yaml):
    """A Bucket named myapp-logs-dev should be defined somewhere under dev/."""
    dev_hits = [p for p in _files_mentioning("myapp-logs-dev") if "dev" in p.parts]
    assert dev_hits, "expected a dev-scoped manifest defining myapp-logs-dev"


def test_prod_logs_bucket_exists(all_yaml):
    """A Bucket named myapp-logs-prod should be defined somewhere under prod/."""
    prod_hits = [p for p in _files_mentioning("myapp-logs-prod") if "prod" in p.parts]
    assert prod_hits, "expected a prod-scoped manifest defining myapp-logs-prod"


def test_bucket_versioning(all_yaml):
    """Both logs buckets should have versioning enabled."""
    assert "versioning" in all_yaml.lower()
    assert "Enabled" in all_yaml


def test_bucket_encryption(all_yaml):
    """Both logs buckets should use AES256 server-side encryption."""
    assert "sseAlgorithm" in all_yaml or "ServerSideEncryption" in all_yaml
    assert "AES256" in all_yaml


def test_public_access_blocked(all_yaml):
    """Both logs buckets should fully block public access."""
    lower = all_yaml.lower()
    assert "publicaccessblock" in lower or "blockpublic" in lower
    assert "true" in lower


def test_logs_scoped_iam_grant_present_both_envs(all_yaml):
    """A policy document should grant access scoped to the new logs bucket
    (not just re-using the existing assets-bucket policy) in both envs."""
    dev_grant = [
        p for p in _files_mentioning("myapp-logs-dev")
        if "dev" in p.parts and ("s3:GetObject" in p.read_text() or "s3:PutObject" in p.read_text())
    ]
    prod_grant = [
        p for p in _files_mentioning("myapp-logs-prod")
        if "prod" in p.parts and ("s3:GetObject" in p.read_text() or "s3:PutObject" in p.read_text())
    ]
    assert dev_grant, "expected an IAM policy granting access to myapp-logs-dev"
    assert prod_grant, "expected an IAM policy granting access to myapp-logs-prod"
