"""Terraform T2-generate: verify module structure.

Located by content rather than by path (issue #72). The prompt asks for "a
Terraform module ... with variables/outputs" supporting "dev/prod via
workspaces or tfvars"; it names no filenames at all, yet this grader used
to require main.tf, infrastructure.tf, variables.tf, dev.tfvars and
prod.tfvars by name, and to `open()` two of them -- so an answer that split
its HCL differently, or took the workspaces branch the prompt explicitly
offers, failed on a convention it was never given.

The seven checks keep their intent, one per assertion: providers/backend
are declared, resources are declared, inputs are declared, dev and prod are
each differentiated somehow, the bucket has versioning, and RDS has
deletion protection.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402

TFVARS_SUFFIXES = (".tfvars", ".json")


@pytest.fixture(scope="module")
def tf_text() -> str:
    return gl.concat_text(".tf")


@pytest.fixture(scope="module")
def tfvars_files() -> list[Path]:
    return [p for p in gl.iter_files(*TFVARS_SUFFIXES) if ".tfvars" in p.name]


def _env_configured(env: str, tfvars_files: list[Path], tf_text: str) -> bool:
    """Whether the answer differentiates `env` at all -- via a tfvars file
    (named for the env, or setting it) or via the workspaces branch the
    prompt offers as the alternative."""
    for p in tfvars_files:
        if env in p.name.lower() or env in gl.read_text(p).lower():
            return True
    if "terraform.workspace" in tf_text and env in tf_text.lower():
        return True
    return False


def test_main_tf_exists(tf_text):
    """Providers and backend should be declared."""
    assert tf_text.strip(), (
        f"no .tf files in the workspace. Workspace contains: {gl.inventory()}"
    )
    assert re.search(r'provider\s+"', tf_text) or re.search(r"terraform\s*\{", tf_text), \
        "expected a provider or terraform block declaring providers/backend"


def test_infrastructure_tf_exists(tf_text):
    """Resources should be declared."""
    assert re.search(r'resource\s+"', tf_text), (
        f"expected at least one `resource \"...\"` block. "
        f"Workspace contains: {gl.inventory()}"
    )


def test_variables_tf_exists(tf_text):
    """Inputs should be declared."""
    assert re.search(r'variable\s+"', tf_text), \
        "expected at least one `variable \"...\"` block declaring module inputs"


def test_dev_tfvars_exists(tfvars_files, tf_text):
    """Dev configuration should be differentiated (tfvars or workspaces)."""
    assert _env_configured("dev", tfvars_files, tf_text), (
        "expected dev to be configured -- a tfvars file for dev, or a "
        f"terraform.workspace branch naming it. tfvars files present: "
        f"{[str(p) for p in tfvars_files]!r}"
    )


def test_prod_tfvars_exists(tfvars_files, tf_text):
    """Prod configuration should be differentiated (tfvars or workspaces)."""
    assert _env_configured("prod", tfvars_files, tf_text), (
        "expected prod to be configured -- a tfvars file for prod, or a "
        f"terraform.workspace branch naming it. tfvars files present: "
        f"{[str(p) for p in tfvars_files]!r}"
    )


def test_s3_bucket_versioning(tf_text):
    """S3 bucket should have versioning enabled"""
    assert "aws_s3_bucket" in tf_text, (
        f"expected an aws_s3_bucket resource. Workspace contains: {gl.inventory()}"
    )
    assert "versioning" in tf_text.lower(), "S3 bucket should have versioning"


def test_rds_deletion_protection(tfvars_files, tf_text):
    """RDS should have deletion protection in prod"""
    haystack = tf_text.lower() + "\n" + "\n".join(
        gl.read_text(p).lower() for p in tfvars_files
    )
    assert "deletion_protection" in haystack or "deletion" in haystack, \
        "Prod should have deletion protection"
