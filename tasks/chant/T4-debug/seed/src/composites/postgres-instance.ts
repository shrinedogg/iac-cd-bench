/**
 * PostgresInstance — an ACK RDS `DBInstance` with its connection-secret
 * plumbing, and the SPEC's RDS guardrails as defaults rather than reminders.
 *
 * SPEC acceptance criterion 2 — deletion protection on, backup retention at
 * least 7 days, not publicly accessible — is enforced here: `deletionProtection`
 * is pinned on, `publiclyAccessible` is pinned off, and a `backupRetentionDays`
 * below the SPEC floor is refused at build time rather than shipped.
 *
 * Members:
 *   instance   K8s::Rds::DBInstance
 *
 * The subnet group is a prop, not a member: the lexicon's ACK RDS coverage is
 * `DBInstance` only — no `DBSubnetGroup` kind exists to declare. See README,
 * "Coverage gaps".
 *
 * ── Secrets ──────────────────────────────────────────────────────────────────
 *
 * Nothing secret-shaped is in this file, this repo, or the build output. The
 * master password and the application's connection string both live in
 * Kubernetes Secrets created out of band; the DBInstance points at the first by
 * name/namespace/key (`masterUserPassword`), and the application consumes the
 * second the same way. That is the referenced-provenance pattern: the estate
 * records that it depends on a secret, never what the secret is.
 *
 * The interim caveat: chant's `declareSecret({ provenance: "referenced" })`
 * primitive, which makes that dependency a first-class lintable declaration,
 * is not in the published `@intentius/chant` 0.46.0 this golden builds
 * against. `secretRef()` (./secrets.ts) is the structural stand-in — same
 * discipline, no primitive. See README, "Coverage gaps".
 */

import { Composite } from "@intentius/chant";
import { DBInstance } from "@intentius/chant-lexicon-k8s";

import {
  DEFAULT_BACKUP_WINDOW,
  DEFAULT_MAINTENANCE_WINDOW,
  DEFAULT_POSTGRES_PORT,
  DEFAULT_POSTGRES_VERSION,
  INFRA_NAMESPACE,
  MINIMUM_BACKUP_RETENTION_DAYS,
  POSTGRES_ENGINE,
  POSTGRES_LOG_EXPORTS,
} from "./defaults.js";
import { ackTags, infraLabels } from "./labels.js";
import { secretRef, type SecretRef } from "./secrets.js";

export interface PostgresInstanceProps {
  /** Instance identifier. Also the ACK custom resource's name. */
  name: string;
  /** Environment identity, stamped as a label and a tag. */
  env: string;
  /** Instance class (SPEC: db.t3.micro in dev, db.t3.medium in prod). */
  instanceClass: string;
  /** Initial database name. */
  databaseName: string;
  /** Master username. Not a secret; the password is. */
  masterUsername: string;
  /**
   * Where the master password lives — a Kubernetes Secret created out of band.
   * Referenced provenance: name, namespace, key. Never a value.
   */
  masterPassword: SecretRef;
  /** Name of the RDS subnet group the instance lands in. */
  dbSubnetGroupName: string;
  /** Allocated storage in GiB. */
  allocatedStorageGiB?: number;
  /** Storage autoscaling ceiling in GiB. */
  maxAllocatedStorageGiB?: number;
  /** Engine version. */
  engineVersion?: string;
  /** Multi-AZ deployment (SPEC: false in dev, true in prod). */
  multiAZ?: boolean;
  /** Encrypt storage at rest (SPEC: required in prod; on by default here). */
  storageEncrypted?: boolean;
  /** KMS key ARN for storage encryption. Omit for the AWS-managed key. */
  kmsKeyID?: string;
  /** Backup retention in days. Must be >= 7 (SPEC criterion 2). */
  backupRetentionDays?: number;
  /** Security groups attached to the instance. */
  vpcSecurityGroupIDs?: string[];
  /** Namespace the ACK custom resources live in. */
  namespace?: string;
  /** Logical component name for labels/tags. */
  component?: string;
}

export const PostgresInstance = Composite<PostgresInstanceProps>((props) => {
  const retention = props.backupRetentionDays ?? MINIMUM_BACKUP_RETENTION_DAYS;
  if (retention < MINIMUM_BACKUP_RETENTION_DAYS) {
    throw new Error(
      `PostgresInstance("${props.name}"): backupRetentionDays must be at least ` +
        `${MINIMUM_BACKUP_RETENTION_DAYS} (SPEC acceptance criterion 2), got ${retention}`,
    );
  }

  const instance = new DBInstance({
    metadata: {
      name: props.name,
      namespace: props.namespace ?? INFRA_NAMESPACE,
      labels: infraLabels(props.component ?? "database", props.env),
    },
    spec: {
      dbInstanceIdentifier: props.name,
      dbInstanceClass: props.instanceClass,
      engine: POSTGRES_ENGINE,
      engineVersion: props.engineVersion ?? DEFAULT_POSTGRES_VERSION,
      dbName: props.databaseName,
      port: DEFAULT_POSTGRES_PORT,
      allocatedStorage: props.allocatedStorageGiB ?? 20,
      maxAllocatedStorage: props.maxAllocatedStorageGiB,
      masterUsername: props.masterUsername,
      // Referenced provenance: a pointer at a Secret created out of band.
      masterUserPassword: secretRef(props.masterPassword),
      multiAZ: props.multiAZ ?? false,
      storageEncrypted: props.storageEncrypted ?? true,
      kmsKeyID: props.kmsKeyID,
      // SPEC criterion 2 — all three clauses, no prop turns them off.
      deletionProtection: true,
      publiclyAccessible: false,
      backupRetentionPeriod: retention,
      preferredBackupWindow: DEFAULT_BACKUP_WINDOW,
      preferredMaintenanceWindow: DEFAULT_MAINTENANCE_WINDOW,
      autoMinorVersionUpgrade: true,
      copyTagsToSnapshot: true,
      enableCloudwatchLogsExports: POSTGRES_LOG_EXPORTS,
      enableIAMDatabaseAuthentication: true,
      dbSubnetGroupName: props.dbSubnetGroupName,
      vpcSecurityGroupIDs: props.vpcSecurityGroupIDs,
      tags: ackTags(props.component ?? "database", props.env),
    },
  });

  return { instance };
}, "PostgresInstance");
