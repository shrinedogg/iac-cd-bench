"""RDS PostgreSQL instance component with deletion protection and backup."""

from __future__ import annotations

from typing import Any

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi.aws.rds import Instance, SubnetGroup

# RDS PostgreSQL engine version; use a recent supported version
ENGINE_VERSION = "17.1"


class RDSInstance(ComponentResource):
    """Manages an RDS PostgreSQL instance with backups and deletion protection.

    Args:
        name: Logical resource name.
        db_name: Database name inside the instance.
        db_user: Master username.
        db_password: Master password (may be an Output for secrets).
        instance_class: EC2 instance class (e.g. "db.t3.micro").
        allocated_storage: Storage in GB.
        multi_az: True for multi-AZ deployment.
        storage_encrypted: True for encrypted storage (prod).
        backup_retention_days: Backup retention window (>= 7).
        subnet_ids: Subnet IDs for the DB subnet group.
        vpc_id: VPC ID for the subnet group.
        tags: Resource tags.
        opts: Pulumi resource options.
    """

    def __init__(
        self,
        name: str,
        *,
        db_name: str,
        db_user: str,
        db_password: str | Output[str],
        instance_class: str = "db.t3.micro",
        allocated_storage: int = 20,
        multi_az: bool = False,
        storage_encrypted: bool = False,
        backup_retention_days: int = 7,
        subnet_ids: list[str] | None = None,
        vpc_id: str | None = None,
        tags: dict[str, str] | None = None,
        opts: ResourceOptions | None = None,
    ) -> None:
        super().__init__("iac-cd-bench:components:RDSInstance", name, None, opts)

        tag_map = tags or {}

        # Subnet group for DB placement
        self._subnet_group: SubnetGroup | None = None
        if vpc_id and subnet_ids:
            self._subnet_group = SubnetGroup(
                f"{name}-subnet-group",
                name=f"{name}-db-subnet-group",
                subnet_ids=subnet_ids,
                tags=tag_map,
                opts=pulumi.ResourceOptions(parent=self),
            )

        self.instance = Instance(
            f"{name}-instance",
            allocated_storage=allocated_storage,
            engine="postgres",
            engine_version=ENGINE_VERSION,
            instance_class=instance_class,
            db_name=db_name,
            username=db_user,
            password=db_password,
            skip_final_snapshot=True,
            deletion_protection=True,
            backup_retention_period=backup_retention_days,
            backup_window="03:00-04:00",
            publicly_accessible=False,
            storage_encrypted=storage_encrypted,
            multi_az=multi_az,
            db_subnet_group_name=(
                self._subnet_group.name if self._subnet_group else None
            ),
            tags=tag_map,
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.endpoint: Output[str] = self.instance.endpoint
        self.address: Output[str] = self.instance.address
        self.port: Output[int] = self.instance.port
        self._db_name: str = db_name

        self.register_outputs(
            {
                "endpoint": self.endpoint,
                "address": self.address,
                "port": self.port,
                "db_name": pulumi.Output.from_input(db_name),
            }
        )

    @property
    def id(self) -> Output[str]:
        return self.instance.id  # type: ignore[no-any-return]

    @property
    def db_name(self) -> str:
        return self._db_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "address": self.address,
            "port": self.port,
        }
