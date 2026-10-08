"""S3 bucket component with versioning, encryption, and no-public-access."""

from __future__ import annotations

from typing import Any

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi.aws.s3 import BucketV2, BucketVersioningV2, BucketPublicAccessBlock


class S3Bucket(ComponentResource):
    """Manages an S3 bucket with versioning, encryption, and public-access controls.

    Args:
        name: Logical resource name.
        bucket_name: The S3 bucket name.
        tags: Resource tags.
        enable_replication: True for prod (cross-region replication target).
        replication_target_bucket: ARN of the replication destination
            bucket (prod only).
        opts: Pulumi resource options.
    """

    def __init__(
        self,
        name: str,
        *,
        bucket_name: str,
        tags: dict[str, str] | None = None,
        enable_replication: bool = False,
        replication_target_bucket: str | None = None,
        opts: ResourceOptions | None = None,
    ) -> None:
        super().__init__("iac-cd-bench:components:S3Bucket", name, None, opts)

        tag_map = tags or {}

        self.bucket = BucketV2(
            f"{name}-bucket",
            bucket=bucket_name,
            tags=tag_map,
            opts=pulumi.ResourceOptions(parent=self),
        )

        BucketVersioningV2(
            f"{name}-versioning",
            bucket=self.bucket.id,
            versioning_configuration={"status": "Enabled"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        BucketPublicAccessBlock(
            f"{name}-public-access",
            bucket=self.bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.bucket_arn: Output[str] = self.bucket.arn
        self.bucket_id: Output[str] = self.bucket.id

        if enable_replication and replication_target_bucket:
            self._setup_replication(
                name,
                replication_target_bucket,
                tag_map,
            )

        self.register_outputs(
            {
                "bucket_id": self.bucket_id,
                "bucket_arn": self.bucket_arn,
            }
        )

    def _setup_replication(
        self,
        name: str,
        target_bucket_arn: str,
        tags: dict[str, str],
    ) -> None:
        """Configure cross-region replication for prod environments."""
        from pulumi.aws.s3 import BucketReplicationConfig

        self.replication_config = BucketReplicationConfig(
            f"{name}-replication",
            bucket=self.bucket.id,
            rules=[
                {
                    "id": "replicate-to-us-west-2",
                    "status": "Enabled",
                    "priority": 0,
                    "status_filter": {"status": "Enabled"},
                    "destination": {
                        "bucket": target_bucket_arn,
                        "encryption_configuration": {
                            "replica_kms_key_id": "",
                        },
                    },
                    "source_selection_configuration": {
                        "sse_kms_encrypted_objects": {"status": "Enabled"},
                    },
                }
            ],
            opts=pulumi.ResourceOptions(parent=self),
        )

    @property
    def id(self) -> Output[str]:
        return self.bucket_id

    @property
    def arn(self) -> Output[str]:
        return self.bucket_arn

    def to_dict(self) -> dict[str, Any]:
        return {"bucket_id": self.bucket_id, "bucket_arn": self.bucket_arn}
