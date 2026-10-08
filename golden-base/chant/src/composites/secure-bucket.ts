/**
 * SecureBucket — an ACK S3 `Bucket` that cannot be created insecurely.
 *
 * SPEC acceptance criterion 1 (versioning on, server-side encryption, no
 * public access) is baked into the factory rather than left to the caller.
 * There is no prop that turns versioning off and no prop that unblocks public
 * access: the posture is the composite, and a reviewer checking criterion 1
 * checks this file once instead of every bucket.
 *
 * Members:
 *   bucket       K8s::S3::Bucket
 *   replicaRole  K8s::Iam::Role     (only when cross-region replication is on)
 *
 * The prod arm of the SPEC matrix adds cross-region replication to us-west-2.
 * That is the one prop that changes the resource set, so it changes the member
 * set too — the replication IAM role only exists when replication does.
 */

import { Composite } from "@intentius/chant";
import { IamRole, S3Bucket } from "@intentius/chant-lexicon-k8s";

import {
  AES256_ENCRYPTION,
  BUCKET_OWNER_ENFORCED,
  INFRA_NAMESPACE,
  PUBLIC_ACCESS_BLOCKED,
  VERSIONING_ENABLED,
} from "./defaults.js";
import { ackTags, infraLabels } from "./labels.js";
import { kmsEncryption, replicationRules, s3ReplicationTrustPolicy } from "./policies.js";

export interface SecureBucketReplication {
  /** ARN of the destination bucket (SPEC prod: a us-west-2 replica). */
  destinationBucketARN: string;
  /** Storage class for replicas. Defaults to STANDARD_IA. */
  storageClass?: string;
  /** Only replicate keys under this prefix. Omit to replicate everything. */
  prefix?: string;
  /** Replicate objects that already existed when replication was enabled. */
  replicateExisting?: boolean;
}

export interface SecureBucketProps {
  /** S3 bucket name. Also the ACK custom resource's name. */
  name: string;
  /** Environment identity, stamped as a label and a tag. */
  env: string;
  /** Region the bucket is created in. */
  region: string;
  /**
   * KMS key ARN for SSE-KMS. Omitted, the bucket uses SSE-S3 (AES-256) —
   * both satisfy SPEC criterion 1; KMS is the prod-grade choice.
   */
  kmsKeyARN?: string;
  /** Cross-region replication. Prod-only in the SPEC matrix. */
  replication?: SecureBucketReplication;
  /** Namespace the ACK custom resources live in. */
  namespace?: string;
  /** Logical component name for labels/tags. */
  component?: string;
}

export const SecureBucket = Composite<SecureBucketProps>((props) => {
  // The replication role exists only when replication does — the prod arm of
  // the SPEC matrix adds both together or neither.
  const replicaRole = props.replication === undefined
    ? undefined
    : new IamRole({
        metadata: {
          name: `${props.name}-replication`,
          namespace: props.namespace ?? INFRA_NAMESPACE,
          labels: infraLabels(props.component ?? "assets", props.env),
        },
        spec: {
          name: `${props.name}-replication`,
          description: `S3 cross-region replication role for ${props.name}`,
          assumeRolePolicyDocument: s3ReplicationTrustPolicy(),
          tags: ackTags(props.component ?? "assets", props.env),
        },
      });

  const bucket = new S3Bucket({
    metadata: {
      name: props.name,
      namespace: props.namespace ?? INFRA_NAMESPACE,
      labels: infraLabels(props.component ?? "assets", props.env),
    },
    spec: {
      name: props.name,
      createBucketConfiguration: { locationConstraint: props.region },
      // SPEC criterion 1, all three clauses, unconditional.
      versioning: VERSIONING_ENABLED,
      encryption: props.kmsKeyARN === undefined
        ? AES256_ENCRYPTION
        : kmsEncryption(props.kmsKeyARN),
      publicAccessBlock: PUBLIC_ACCESS_BLOCKED,
      ownershipControls: BUCKET_OWNER_ENFORCED,
      ...(props.replication !== undefined
        ? {
            replication: {
              roleRef: { from: { name: `${props.name}-replication` } },
              rules: replicationRules(props.replication),
            },
          }
        : {}),
      tagging: { tagSet: ackTags(props.component ?? "assets", props.env) },
    },
  });

  return { bucket, ...(replicaRole !== undefined ? { replicaRole } : {}) };
}, "SecureBucket");
