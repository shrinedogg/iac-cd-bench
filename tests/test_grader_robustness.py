"""Both-direction checks on the graders rewritten for issue #72.

A grader that stopped rejecting correct answers is only half fixed; the
other half is that it still rejects wrong ones. Every case below builds a
throwaway workspace by hand, runs the real grader against it under the same
pytest invocation bench.stages.semantic.run_semantic uses, and asserts on
individual test outcomes rather than on the pass count.

Three invariants:

* A correct answer written to a NON-canonical layout passes in full. This
  is the regression #72 was filed for: knr-ops T2-generate scored 0/6 on a
  run whose lint and static both passed, because it read
  `infra/s3/logs-bucket.yaml` and the model had written
  `infra/s3/logs/bucket.yaml`.
* A wrong answer -- a missing property, a wrong value, an untouched seed --
  still fails, and fails on the assertion that targets that defect rather
  than taking the module down with it.
* No grader raises on a missing artifact. That is the mechanism behind the
  0/6: `read_text()` on an absent path is an exception, not an assertion
  failure, so one wrong guess about the layout errored all six assertions
  at once.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "tasks"

_OUTCOME_RE = re.compile(r"::(\w+)\s+(PASSED|FAILED|ERROR)")

_LAST_LOG = ""


def grade(task: str, files: dict[str, str]) -> dict[str, str]:
    """Run one task's real grader against a fabricated workspace.

    The pytest invocation matches bench.stages.semantic.run_semantic's --
    same interpreter, same cwd-is-the-workspace convention -- but the full
    output is kept rather than the last 2000 characters the stage records,
    so per-assertion outcomes can be read off reliably.

    Returns {test_name: PASSED|FAILED|ERROR}.
    """
    global _LAST_LOG
    task_dir = TASKS / task
    ws = Path(tempfile.mkdtemp(prefix="gradercheck-"))
    try:
        for rel, body in files.items():
            dest = ws / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=line",
             "-p", "no:cacheprovider", str(task_dir / "tests" / "test_task.py")],
            capture_output=True, text=True, timeout=120, cwd=str(ws),
        )
        _LAST_LOG = proc.stdout + proc.stderr
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    outcomes = dict(
        (m.group(1), m.group(2)) for m in _OUTCOME_RE.finditer(_LAST_LOG)
    )
    assert outcomes, f"no test outcomes parsed from grader output:\n{_LAST_LOG}"
    return outcomes


def last_log() -> str:
    return _LAST_LOG


def assert_all(outcomes: dict[str, str], verdict: str) -> None:
    wrong = {k: v for k, v in outcomes.items() if v != verdict}
    assert not wrong, f"expected every assertion {verdict}, got {wrong!r}"


def assert_none_passed(outcomes: dict[str, str], expected_count: int) -> None:
    """No assertion passed, and every one of them was still collected and
    reported -- a grader must not lose assertions when the artifact is
    missing, because the completeness axis divides by the reported total."""
    passed = {k for k, v in outcomes.items() if v == "PASSED"}
    assert not passed, f"assertions passed on an empty workspace: {passed!r}"
    assert len(outcomes) == expected_count, (
        f"expected {expected_count} assertions reported, got {len(outcomes)}: {outcomes!r}"
    )


def assert_only_failed(outcomes: dict[str, str], *names: str) -> None:
    """The named assertions failed and nothing else did -- a wrong answer
    must fail on the assertion that targets its specific defect, not take
    the whole module down with it."""
    failed = {k for k, v in outcomes.items() if v != "PASSED"}
    assert failed == set(names), f"expected exactly {set(names)!r} to fail, got {failed!r}"
    assert "ERROR" not in outcomes.values(), f"grader errored rather than failed: {outcomes!r}"


# ──────────────────────────────────────────────────────────────────────────
# knr-ops T2-generate — the grader #72 was filed against
# ──────────────────────────────────────────────────────────────────────────

# Written to the layout the failing v2 run actually chose (infra/s3/logs/…,
# infra/iam/logs-irsa/…, flux/logs-bucket-kustomization.yaml), not the
# layout the old grader demanded.
KNR_BUCKET = """\
apiVersion: s3.aws.upbound.io/v1beta1
kind: Bucket
metadata:
  name: logs-bucket
spec:
  forProvider:
    region: us-east-1
---
apiVersion: s3.aws.upbound.io/v1beta1
kind: BucketVersioning
metadata:
  name: logs-bucket-versioning
spec:
  forProvider:
    bucketRef:
      name: logs-bucket
    versioningConfiguration:
      - status: Enabled
---
apiVersion: s3.aws.upbound.io/v1beta1
kind: BucketServerSideEncryptionConfiguration
metadata:
  name: logs-bucket-encryption
spec:
  forProvider:
    bucketRef:
      name: logs-bucket
    rule:
      - applyServerSideEncryptionByDefault:
          - sseAlgorithm: AES256
---
apiVersion: s3.aws.upbound.io/v1beta1
kind: BucketPublicAccessBlock
metadata:
  name: logs-bucket-pab
spec:
  forProvider:
    bucketRef:
      name: logs-bucket
    blockPublicAcls: true
    blockPublicPolicy: true
    ignorePublicAcls: true
    restrictPublicBuckets: true
"""

KNR_IAM = """\
apiVersion: iam.aws.upbound.io/v1beta1
kind: Role
metadata:
  name: logs-bucket-irsa
spec:
  forProvider:
    assumeRolePolicy: |
      {"Version": "2012-10-17"}
"""

KNR_FLUX = """\
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: logs-bucket
  namespace: flux-system
spec:
  interval: 5m0s
  path: ./overlays/dev/logs-bucket
  prune: true
  sourceRef:
    kind: GitRepository
    name: myapp-infra
"""


def _knr_files(**overrides: str | None) -> dict[str, str]:
    files = {
        "infra/s3/logs/bucket.yaml": KNR_BUCKET,
        "infra/iam/logs-irsa/role.yaml": KNR_IAM,
        "flux/logs-bucket-kustomization.yaml": KNR_FLUX,
        "model_output.md": "see files",
    }
    for k, v in overrides.items():
        key = {
            "bucket": "infra/s3/logs/bucket.yaml",
            "iam": "infra/iam/logs-irsa/role.yaml",
            "flux": "flux/logs-bucket-kustomization.yaml",
        }[k]
        if v is None:
            files.pop(key)
        else:
            files[key] = v
    return files


def test_knrops_t2_correct_answer_in_noncanonical_layout_passes():
    assert_all(grade("knr-ops/T2-generate", _knr_files()), "PASSED")


def test_knrops_t2_empty_workspace_fails_cleanly():
    outcomes = grade("knr-ops/T2-generate", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 6)


def test_knrops_t2_missing_versioning_fails_only_that():
    bucket = KNR_BUCKET.replace(
        """---
apiVersion: s3.aws.upbound.io/v1beta1
kind: BucketVersioning
metadata:
  name: logs-bucket-versioning
spec:
  forProvider:
    bucketRef:
      name: logs-bucket
    versioningConfiguration:
      - status: Enabled
""",
        "",
    )
    assert "BucketVersioning" not in bucket
    assert_only_failed(grade("knr-ops/T2-generate", _knr_files(bucket=bucket)),
                       "test_bucket_versioning")


def test_knrops_t2_wrong_encryption_algorithm_fails_only_that():
    bucket = KNR_BUCKET.replace("sseAlgorithm: AES256", "sseAlgorithm: aws:kms")
    assert_only_failed(grade("knr-ops/T2-generate", _knr_files(bucket=bucket)),
                       "test_bucket_encryption")


def test_knrops_t2_public_access_left_open_fails_only_that():
    bucket = KNR_BUCKET.replace("blockPublicPolicy: true", "blockPublicPolicy: false")
    assert_only_failed(grade("knr-ops/T2-generate", _knr_files(bucket=bucket)),
                       "test_public_access_blocked")


def test_knrops_t2_missing_iam_role_fails_only_that():
    assert_only_failed(grade("knr-ops/T2-generate", _knr_files(iam=None)),
                       "test_iam_role_created")


def test_knrops_t2_missing_flux_kustomization_fails_only_that():
    assert_only_failed(grade("knr-ops/T2-generate", _knr_files(flux=None)),
                       "test_flux_kustomization_added")


def test_knrops_t2_assets_bucket_does_not_satisfy_logs_bucket():
    """The grader must not be satisfied by a perfectly-formed answer about
    the wrong bucket -- content-based location is not a workspace-wide grep."""
    files = _knr_files(
        bucket=KNR_BUCKET.replace("logs-bucket", "assets-bucket"),
        iam=KNR_IAM.replace("logs-bucket", "assets-bucket"),
        flux=KNR_FLUX.replace("logs-bucket", "assets-bucket").replace("logs", "assets"),
    )
    outcomes = grade("knr-ops/T2-generate", files)
    assert_all(outcomes, "FAILED")


# ──────────────────────────────────────────────────────────────────────────
# crossplane T2-generate / T4-debug
# ──────────────────────────────────────────────────────────────────────────

XRD = """\
apiVersion: apiextensions.crossplane.io/v1
kind: CompositeResourceDefinition
metadata:
  name: xwebservices.platform.example.com
spec:
  group: platform.example.com
  names:
    kind: XWebService
    plural: xwebservices
"""

COMPOSITION = """\
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: webservice-aws
spec:
  compositeTypeRef:
    apiVersion: platform.example.com/v1
    kind: XWebService
  mode: Pipeline
"""

CLAIM = """\
apiVersion: platform.example.com/v1
kind: XWebService
metadata:
  name: web-dev
spec:
  environment: dev
"""

PROVIDER_CONFIG = """\
apiVersion: aws.upbound.io/v1beta1
kind: ProviderConfig
metadata:
  name: dev-us-east-1
spec:
  credentials:
    source: Secret
"""


def _xp_files(**overrides):
    files = {
        "platform/api/definition.yaml": XRD,
        "platform/impl/aws.yaml": COMPOSITION,
        "envs/dev.yaml": CLAIM,
        "platform/creds.yaml": PROVIDER_CONFIG,
        "model_output.md": "see files",
    }
    key_map = {"xrd": "platform/api/definition.yaml",
               "composition": "platform/impl/aws.yaml",
               "claim": "envs/dev.yaml",
               "provider": "platform/creds.yaml"}
    for k, v in overrides.items():
        if v is None:
            files.pop(key_map[k])
        else:
            files[key_map[k]] = v
    return files


def test_crossplane_t2_correct_answer_in_noncanonical_layout_passes():
    assert_all(grade("crossplane/T2-generate", _xp_files()), "PASSED")


def test_crossplane_t2_empty_workspace_fails_cleanly():
    outcomes = grade("crossplane/T2-generate", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 4)


def test_crossplane_t2_missing_provider_config_fails_only_that():
    assert_only_failed(grade("crossplane/T2-generate", _xp_files(provider=None)),
                       "test_provider_config_exists")


def test_crossplane_t2_unrelated_instance_does_not_count_as_claim():
    """A ConfigMap is not an instance of the composite API the XRD defines."""
    unrelated = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: nope\n"
    assert_only_failed(grade("crossplane/T2-generate", _xp_files(claim=unrelated)),
                       "test_claim_exists")


XP_SEED_COMPOSITION = (TASKS / "crossplane/T4-debug/seed/composition.yaml").read_text()

XP_FIXED_COMPOSITION = XP_SEED_COMPOSITION.replace(
    "spec.parameters.storageClass", "spec.parameters.instanceClass"
).replace(
    "            patches:",
    "            readinessChecks:\n"
    "              - type: MatchCondition\n"
    "                matchCondition:\n"
    "                  type: Ready\n"
    "                  status: \"True\"\n"
    "            patches:",
)


def test_crossplane_t4_fix_at_any_path_passes():
    assert_all(
        grade("crossplane/T4-debug",
              {"fixed/composition-v2.yaml": XP_FIXED_COMPOSITION,
               "model_output.md": "see files"}),
        "PASSED",
    )


def test_crossplane_t4_unmodified_seed_fails_both():
    outcomes = grade("crossplane/T4-debug",
                     {"composition.yaml": XP_SEED_COMPOSITION,
                      "model_output.md": "no change"})
    assert_all(outcomes, "FAILED")


def test_crossplane_t4_empty_workspace_fails_cleanly():
    outcomes = grade("crossplane/T4-debug", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 2)


# ──────────────────────────────────────────────────────────────────────────
# pulumi-python T2-generate / T4-debug
# ──────────────────────────────────────────────────────────────────────────

PY_BUCKET = """\
import pulumi
import pulumi_aws as aws


class BucketComponent(pulumi.ComponentResource):
    def __init__(self, name, env, opts=None):
        super().__init__("myapp:storage:Bucket", name, {}, opts)
        self.bucket = aws.s3.Bucket(
            f"{name}-bucket",
            bucket=f"myapp-assets-{env}",
            versioning=aws.s3.BucketVersioningArgs(enabled=True),
            opts=pulumi.ResourceOptions(parent=self),
        )
"""

PY_RDS = """\
import pulumi
import pulumi_aws as aws


class RdsComponent(pulumi.ComponentResource):
    def __init__(self, name, deletion_protection=True, opts=None):
        super().__init__("myapp:db:Rds", name, {}, opts)
        self.db = aws.rds.Instance(
            f"{name}-db",
            instance_class="db.t3.medium",
            deletion_protection=deletion_protection,
            opts=pulumi.ResourceOptions(parent=self),
        )
"""

PY_MAIN = """\
import pulumi
from infra.storage import BucketComponent
from infra.db import RdsComponent

env = pulumi.Config().require("env")
BucketComponent("assets", env)
RdsComponent("main", deletion_protection=env == "prod")
"""


def _pypy_files(**overrides):
    files = {
        "app/program.py": PY_MAIN,
        "app/infra/storage.py": PY_BUCKET,
        "app/infra/db.py": PY_RDS,
        "model_output.md": "see files",
    }
    key_map = {"main": "app/program.py", "bucket": "app/infra/storage.py",
               "rds": "app/infra/db.py"}
    for k, v in overrides.items():
        if v is None:
            files.pop(key_map[k])
        else:
            files[key_map[k]] = v
    return files


def test_pulumi_python_t2_correct_answer_in_noncanonical_layout_passes():
    assert_all(grade("pulumi-python/T2-generate", _pypy_files()), "PASSED")


def test_pulumi_python_t2_empty_workspace_fails_cleanly():
    outcomes = grade("pulumi-python/T2-generate", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 6)


def test_pulumi_python_t2_bucket_without_versioning_fails_only_that():
    bucket = PY_BUCKET.replace(
        "            versioning=aws.s3.BucketVersioningArgs(enabled=True),\n", "")
    assert "versioning" not in bucket
    assert_only_failed(grade("pulumi-python/T2-generate", _pypy_files(bucket=bucket)),
                       "test_bucket_versioning")


def test_pulumi_python_t2_flat_program_without_components_fails_only_that():
    flat = PY_BUCKET.replace("class BucketComponent(pulumi.ComponentResource):",
                             "class BucketComponent:").replace(
        "        super().__init__(\"myapp:storage:Bucket\", name, {}, opts)\n", "")
    rds = PY_RDS.replace("class RdsComponent(pulumi.ComponentResource):",
                         "class RdsComponent:").replace(
        "        super().__init__(\"myapp:db:Rds\", name, {}, opts)\n", "")
    assert_only_failed(
        grade("pulumi-python/T2-generate", _pypy_files(bucket=flat, rds=rds)),
        "test_components_dir",
    )


PY_SEED = (TASKS / "pulumi-python/T4-debug/seed/__main__.py").read_text()

# A clean rewrite, which is what a real fix looks like: the seed's own
# "# Defect 2: .apply() result used..." comments each contain the literal
# `.apply(` that this grader counts, so an answer that preserves the seed's
# commentary trips the count check on comments alone. That looseness
# predates #72 and is left as-is; the fixture just has to be a realistic
# answer rather than a minimal diff.
PY_FIXED = """\
import pulumi
import pulumi_aws as aws

config = pulumi.Config()
env = config.get("env")
region = config.get("region", "us-east-1")

db_password = config.require_secret("dbPassword")

bucket = aws.s3.Bucket(
    "app-bucket",
    bucket=f"myapp-assets-{env}",
    versioning=aws.s3.BucketVersioningArgs(enabled=True),
    tags={"Environment": env, "Project": "myapp"},
)

bucket_url = bucket.arn.apply(
    lambda arn: f"https://s3.{region}.amazonaws.com/{arn}")

db = aws.rds.Instance(
    "app-db",
    instance_class="db.t3.medium",
    engine="postgres",
    engine_version="16.1",
    allocated_storage=20,
    db_name="appdb",
    username="app-user",
    password=db_password,
    skip_final_snapshot=True,
    deletion_protection=True,
)

pulumi.export("bucketUrl", bucket_url)
pulumi.export("dbEndpoint", db.endpoint)
"""


def test_pulumi_python_t4_fix_at_any_path_passes():
    assert_all(
        grade("pulumi-python/T4-debug",
              {"stack/entry.py": PY_FIXED, "model_output.md": "see files"}),
        "PASSED",
    )


def test_pulumi_python_t4_empty_workspace_fails_cleanly():
    outcomes = grade("pulumi-python/T4-debug", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 2)


def test_pulumi_python_t4_apply_overuse_still_fails():
    overused = PY_FIXED + "\nx = db.endpoint.apply(str).apply(str).apply(str)\n"
    outcomes = grade("pulumi-python/T4-debug",
                     {"stack/entry.py": overused, "model_output.md": "see files"})
    assert_only_failed(outcomes, "test_apply_not_on_output")


# ──────────────────────────────────────────────────────────────────────────
# pulumi-typescript T2-generate / T4-debug
# ──────────────────────────────────────────────────────────────────────────

TS_BUCKET = """\
import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

export class BucketComponent extends pulumi.ComponentResource {
    constructor(name: string, env: string, opts?: pulumi.ComponentResourceOptions) {
        super("myapp:storage:Bucket", name, {}, opts);
        new aws.s3.Bucket(`${name}-bucket`, {
            bucket: `myapp-assets-${env}`,
            versioning: { enabled: true },
        }, { parent: this });
    }
}
"""

TS_RDS = """\
import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

export class RdsComponent extends pulumi.ComponentResource {
    constructor(name: string, deletionProtection: boolean, opts?: pulumi.ComponentResourceOptions) {
        super("myapp:db:Rds", name, {}, opts);
        new aws.rds.Instance(`${name}-db`, {
            instanceClass: "db.t3.medium",
            deletionProtection,
        }, { parent: this });
    }
}
"""

TS_MAIN = """\
import * as pulumi from "@pulumi/pulumi";
import { BucketComponent } from "./infra/storage";
import { RdsComponent } from "./infra/db";

const env = new pulumi.Config().require("env");
new BucketComponent("assets", env);
new RdsComponent("main", env === "prod");
"""


def _pyts_files(**overrides):
    files = {
        "app/program.ts": TS_MAIN,
        "app/infra/storage.ts": TS_BUCKET,
        "app/infra/db.ts": TS_RDS,
        "model_output.md": "see files",
    }
    key_map = {"main": "app/program.ts", "bucket": "app/infra/storage.ts",
               "rds": "app/infra/db.ts"}
    for k, v in overrides.items():
        if v is None:
            files.pop(key_map[k])
        else:
            files[key_map[k]] = v
    return files


def test_pulumi_ts_t2_correct_answer_in_noncanonical_layout_passes():
    assert_all(grade("pulumi-typescript/T2-generate", _pyts_files()), "PASSED")


def test_pulumi_ts_t2_empty_workspace_fails_cleanly():
    outcomes = grade("pulumi-typescript/T2-generate", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 6)


def test_pulumi_ts_t2_rds_without_deletion_protection_fails_only_that():
    rds = """\
import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

export class RdsComponent extends pulumi.ComponentResource {
    constructor(name: string, opts?: pulumi.ComponentResourceOptions) {
        super("myapp:db:Rds", name, {}, opts);
        new aws.rds.Instance(`${name}-db`, {
            instanceClass: "db.t3.medium",
        }, { parent: this });
    }
}
"""
    assert "deletionProtection" not in rds
    assert_only_failed(grade("pulumi-typescript/T2-generate", _pyts_files(rds=rds)),
                       "test_rds_deletion_protection")


TS_SEED = (TASKS / "pulumi-typescript/T4-debug/seed/index.ts").read_text()

# A clean rewrite, for the same reason as PY_FIXED: the seed's own defect
# comments contain the literal strings ("async function", "pulumi.output")
# this grader greps for, so a minimal diff that keeps the commentary trips
# the check on comments alone. Pre-existing, and out of scope for #72.
TS_FIXED = """\
import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

const config = new pulumi.Config();
const env = config.get("env") || "dev";
const region = config.get("region") || "us-east-1";

const bucket = new aws.s3.Bucket("app-bucket", {
    bucket: `myapp-assets-${env}`,
    versioning: { enabled: true },
    tags: { Environment: env, Project: "myapp" },
});

const bucketUrl = bucket.arn.apply(
    (arn) => `https://s3.${region}.amazonaws.com/${arn}`);

const dbPassword = config.requireSecret("dbPassword");

const db = new aws.rds.Instance("app-db", {
    instanceClass: "db.t3.medium",
    engine: "postgres",
    engineVersion: "16.1",
    allocatedStorage: 20,
    dbName: "appdb",
    username: "app-user",
    password: dbPassword,
    skipFinalSnapshot: true,
    deletionProtection: true,
});

export const bucketEndpoint = bucketUrl;
export const dbEndpoint = db.endpoint;
"""


def test_pulumi_ts_t4_fix_at_any_path_passes():
    assert "await arn" not in TS_FIXED
    assert_all(
        grade("pulumi-typescript/T4-debug",
              {"stack/entry.ts": TS_FIXED, "model_output.md": "see files"}),
        "PASSED",
    )


def test_pulumi_ts_t4_unmodified_seed_still_fails_the_async_check():
    """An answer that changed nothing must not pass.

    Only the async/output assertion fails here: the seed's `.apply()`
    mention inside a comment already satisfies test_proper_output_handling.
    That pre-existing looseness is recorded rather than fixed -- #72 is
    about graders rejecting correct answers -- but the seed must still fail
    the grader overall, and it does."""
    outcomes = grade("pulumi-typescript/T4-debug",
                     {"index.ts": TS_SEED, "model_output.md": "no change"})
    assert_only_failed(outcomes, "test_no_async_in_index")


def test_pulumi_ts_t4_empty_workspace_fails_cleanly():
    outcomes = grade("pulumi-typescript/T4-debug", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 2)


# ──────────────────────────────────────────────────────────────────────────
# terraform T2-generate / T4-debug
# ──────────────────────────────────────────────────────────────────────────

TF_ROOT = """\
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "env" {
  type = string
}
"""

TF_RESOURCES = """\
resource "aws_s3_bucket" "assets" {
  bucket = "myapp-assets-${var.env}"
}

resource "aws_s3_bucket_versioning" "assets" {
  bucket = aws_s3_bucket.assets.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_db_instance" "main" {
  identifier          = "${var.env}-db"
  instance_class      = var.instance_class
  deletion_protection = var.deletion_protection
}
"""

TF_DEV = 'env = "dev"\ninstance_class = "db.t3.small"\ndeletion_protection = false\n'
TF_PROD = 'env = "prod"\ninstance_class = "db.t3.medium"\ndeletion_protection = true\n'


def _tf_files(**overrides):
    files = {
        "stack/providers.tf": TF_ROOT,
        "stack/resources.tf": TF_RESOURCES,
        "envs/development.tfvars": TF_DEV,
        "envs/production.tfvars": TF_PROD,
        "model_output.md": "see files",
    }
    key_map = {"root": "stack/providers.tf", "resources": "stack/resources.tf",
               "dev": "envs/development.tfvars", "prod": "envs/production.tfvars"}
    for k, v in overrides.items():
        if v is None:
            files.pop(key_map[k])
        else:
            files[key_map[k]] = v
    return files


def test_terraform_t2_correct_answer_in_noncanonical_layout_passes():
    """Filenames here are deliberately none of main.tf / infrastructure.tf /
    variables.tf / dev.tfvars / prod.tfvars -- the five the old grader
    required by name, none of which the prompt asks for."""
    assert_all(grade("terraform/T2-generate", _tf_files()), "PASSED")


def test_terraform_t2_empty_workspace_fails_cleanly():
    outcomes = grade("terraform/T2-generate", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 7)


def test_terraform_t2_missing_prod_config_fails_only_that():
    assert_only_failed(grade("terraform/T2-generate", _tf_files(prod=None)),
                       "test_prod_tfvars_exists")


def test_terraform_t2_bucket_without_versioning_fails_only_that():
    resources = TF_RESOURCES.replace(
        """resource "aws_s3_bucket_versioning" "assets" {
  bucket = aws_s3_bucket.assets.id
  versioning_configuration {
    status = "Enabled"
  }
}

""", "")
    assert "versioning" not in resources
    assert_only_failed(grade("terraform/T2-generate", _tf_files(resources=resources)),
                       "test_s3_bucket_versioning")


def test_terraform_t4_fix_at_any_path_passes():
    """The point of this test is path independence (#72): a correct fix must
    be graded wherever the model puts it.

    The fixture has to be a *complete* fix. It used to flip
    deletion_protection alone and still pass, because the grader's other two
    assertions matched strings the seed already contained (#75). Now all three
    seeded defects have to be addressed: the destroyable database, the
    count/for_each mismatch, and the output indexing a counted resource.
    """
    fixed = (TASKS / "terraform/T4-debug/seed/main.tf").read_text()
    fixed = fixed.replace("deletion_protection = false", "deletion_protection = true")
    fixed = fixed.replace("count = length(var.envs)", "for_each = toset(var.envs)")
    fixed = fixed.replace(
        "aws_db_instance.main[0].endpoint",
        "{ for k, v in aws_db_instance.main : k => v.endpoint }")
    assert_all(
        grade("terraform/T4-debug", {"infra/db.tf": fixed, "model_output.md": "see files"}),
        "PASSED",
    )


def test_terraform_t4_empty_workspace_fails_cleanly():
    outcomes = grade("terraform/T4-debug", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 3)


# ──────────────────────────────────────────────────────────────────────────
# bare T3-modify / T4-debug
# ──────────────────────────────────────────────────────────────────────────

BARE_DEV_SEED = (TASKS / "bare/T3-modify/seed/dev/workers.yaml").read_text()
BARE_PROD_SEED = (TASKS / "bare/T3-modify/seed/prod/workers.yaml").read_text()


def _bare_t3_files(dev_replicas: int = 3, prod_replicas: int = 6,
                   dev_type: str = "t3.medium", **_):
    dev = BARE_DEV_SEED.replace("replicas: 2", f"replicas: {dev_replicas}") \
                       .replace("instanceType: t3.medium", f"instanceType: {dev_type}")
    prod = re.sub(r"replicas: \d+", f"replicas: {prod_replicas}", BARE_PROD_SEED)
    # Deliberately NOT dev/workers.yaml and prod/workers.yaml.
    return {
        "clusters/dev/machine-deployment.yaml": dev,
        "clusters/prod/machine-deployment.yaml": prod,
        "model_output.md": "see files",
    }


def test_bare_t3_correct_answer_in_noncanonical_layout_passes():
    assert_all(grade("bare/T3-modify", _bare_t3_files()), "PASSED")


def test_bare_t3_empty_workspace_fails_cleanly():
    outcomes = grade("bare/T3-modify", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 4)


def test_bare_t3_unscaled_dev_fails_only_that():
    assert_only_failed(grade("bare/T3-modify", _bare_t3_files(dev_replicas=2)),
                       "test_dev_replicas_scaled")


def test_bare_t3_instance_type_changed_fails_only_that():
    assert_only_failed(grade("bare/T3-modify", _bare_t3_files(dev_type="t3.xlarge")),
                       "test_dev_instance_type_unchanged")


BARE_T4_SEED = (TASKS / "bare/T4-debug/seed/prod/app.yaml").read_text()
BARE_T4_FIXED = BARE_T4_SEED.replace(
    """      labels:
        app: myapp
        env: prod
    spec:
      serviceAccountName: myapp-prod""",
    """      labels:
        app: myapp
        env: prod
        version: v2
    spec:
      serviceAccountName: myapp-prod""",
)


def test_bare_t4_fix_at_any_path_passes():
    assert BARE_T4_FIXED != BARE_T4_SEED
    assert_all(
        grade("bare/T4-debug",
              {"manifests/production/app.yaml": BARE_T4_FIXED,
               "model_output.md": "see files"}),
        "PASSED",
    )


def test_bare_t4_unmodified_seed_fails_the_selector_check():
    assert_only_failed(
        grade("bare/T4-debug",
              {"prod/app.yaml": BARE_T4_SEED, "model_output.md": "no change"}),
        "test_selector_matches_template_labels",
    )


def test_bare_t4_empty_workspace_fails_cleanly():
    outcomes = grade("bare/T4-debug", {"model_output.md": "nothing usable"})
    assert_none_passed(outcomes, 2)


# ──────────────────────────────────────────────────────────────────────────
# Every grader in the repo: a missing artifact must never ERROR
# ──────────────────────────────────────────────────────────────────────────

ALL_GRADERS = sorted(
    str(p.parents[1].relative_to(TASKS)) for p in TASKS.rglob("tests/test_task.py")
)


@pytest.mark.parametrize("task", ALL_GRADERS)
def test_no_grader_crashes_on_an_empty_workspace(task):
    """The #72 failure mode, checked across every arm.

    A grader that reads a hardcoded path raises FileNotFoundError on an
    answer that chose a different layout. Two things go wrong at once: the
    exception is not an assertion failure, so it says nothing about what
    the answer got wrong; and it takes out every assertion in the module,
    turning one layout disagreement into 0/6.

    An empty workspace is the extreme case of a missing artifact, and no
    grader may respond to it with an exception. Cleanly failing every
    assertion is correct; a fixture that calls pytest.fail (which pytest
    reports as an error at setup, with the right counts) is fine too.
    """
    grade(task, {"model_output.md": "The model produced nothing usable."})
    log = last_log()
    for crash in ("FileNotFoundError", "NotADirectoryError", "IsADirectoryError",
                  "PermissionError", "KeyError", "TypeError", "AttributeError",
                  "IndexError", "yaml.scanner", "INTERNALERROR"):
        assert crash not in log, (
            f"{task}: grader raised {crash} on an empty workspace instead of "
            f"reporting a missing artifact as an assertion failure:\n{log[-1500:]}"
        )
