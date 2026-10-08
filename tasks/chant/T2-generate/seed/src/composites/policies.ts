/**
 * IAM policy documents and other prop shapes built from props.
 *
 * ACK's `Role.assumeRolePolicyDocument` and `Policy.policyDocument` are JSON
 * *strings*, not structured fields — the CRD schema types them as `string`, so
 * WK8501/WK8502 have nothing to check inside them. Building them here, from
 * typed inputs, is the closest thing to type safety the API allows, and it
 * keeps the array transforms (`.map`) that build a statement list out of the
 * composite factories, where EVL010 forbids them.
 */

import { S3_READER_ACTIONS } from "./defaults.js";

const POLICY_VERSION = "2012-10-17";

/** How a role is assumed. `oidc` is the IRSA/EKS-pod-identity path. */
export type TrustMode = "account" | "oidc";

export interface AccountTrust {
  mode: "account";
  /** AWS account whose principals may assume the role. */
  accountID: string;
  /** Require an external ID on the AssumeRole call. */
  externalID?: string;
}

export interface OidcTrust {
  mode: "oidc";
  /** ARN of the cluster's IAM OIDC provider. */
  providerARN: string;
  /** OIDC issuer host, e.g. `oidc.eks.us-east-1.amazonaws.com/id/EXAMPLED539`. */
  issuerHost: string;
  /** Kubernetes namespace of the service account allowed to assume the role. */
  serviceAccountNamespace: string;
  /** Kubernetes service account name allowed to assume the role. */
  serviceAccountName: string;
}

export type RoleTrust = AccountTrust | OidcTrust;

/**
 * The trust policy for a role.
 *
 * The `oidc` arm pins both `:sub` and `:aud`. Pinning only `:aud` (or using a
 * `StringLike` on `:sub`) is the classic IRSA over-trust: every service
 * account in the cluster can then assume the role.
 */
export function assumeRolePolicy(trust: RoleTrust): string {
  if (trust.mode === "oidc") {
    return JSON.stringify({
      Version: POLICY_VERSION,
      Statement: [
        {
          Effect: "Allow",
          Principal: { Federated: trust.providerARN },
          Action: "sts:AssumeRoleWithWebIdentity",
          Condition: {
            StringEquals: {
              [`${trust.issuerHost}:sub`]:
                `system:serviceaccount:${trust.serviceAccountNamespace}:${trust.serviceAccountName}`,
              [`${trust.issuerHost}:aud`]: "sts.amazonaws.com",
            },
          },
        },
      ],
    });
  }
  // `JSON.stringify` drops an undefined value, so an absent external ID leaves
  // no `Condition` key behind — no spread needed (EVL004).
  const condition = trust.externalID === undefined
    ? undefined
    : { StringEquals: { "sts:ExternalId": trust.externalID } };
  return JSON.stringify({
    Version: POLICY_VERSION,
    Statement: [
      {
        Effect: "Allow",
        Principal: { AWS: `arn:aws:iam::${trust.accountID}:root` },
        Action: "sts:AssumeRole",
        Condition: condition,
      },
    ],
  });
}

/** Trust policy letting the S3 service assume a replication role. */
export function s3ReplicationTrustPolicy(): string {
  return JSON.stringify({
    Version: POLICY_VERSION,
    Statement: [
      {
        Effect: "Allow",
        Principal: { Service: "s3.amazonaws.com" },
        Action: "sts:AssumeRole",
      },
    ],
  });
}

export interface ReaderPolicyInput {
  /** Bucket the reader may read from. */
  bucketName: string;
  /** Restrict object reads to this key prefix. Omit for the whole bucket. */
  prefix?: string;
  /** Extra, explicitly enumerated actions. Wildcards fail SPEC criterion 4. */
  additionalActions?: string[];
}

/**
 * A read-only S3 policy with no wildcard action and no wildcard resource.
 *
 * `ListBucket` is scoped to the bucket ARN; the object actions are scoped to
 * the key space. Splitting them is what keeps `Resource: "*"` out of the
 * document — the shortcut every review catches.
 */
export function readerPolicyDocument(input: ReaderPolicyInput): string {
  const bucketARN = `arn:aws:s3:::${input.bucketName}`;
  const objectARN = input.prefix === undefined
    ? `${bucketARN}/*`
    : `${bucketARN}/${input.prefix}/*`;
  const objectActions = S3_READER_ACTIONS.filter((action: string) => action !== "s3:ListBucket");
  const actions = input.additionalActions === undefined
    ? objectActions
    : objectActions.concat(input.additionalActions);
  return JSON.stringify({
    Version: POLICY_VERSION,
    Statement: [
      {
        Sid: "ListAssetBucket",
        Effect: "Allow",
        Action: ["s3:ListBucket"],
        Resource: [bucketARN],
      },
      {
        Sid: "ReadAssetObjects",
        Effect: "Allow",
        Action: actions,
        Resource: [objectARN],
      },
    ],
  });
}

/** SSE-KMS encryption block for an ACK S3 bucket. */
export function kmsEncryption(kmsKeyARN: string): Record<string, unknown> {
  return {
    rules: [
      {
        applyServerSideEncryptionByDefault: {
          sseAlgorithm: "aws:kms",
          kmsMasterKeyID: kmsKeyARN,
        },
        bucketKeyEnabled: true,
      },
    ],
  };
}

export interface ReplicationInput {
  destinationBucketARN: string;
  storageClass?: string;
  prefix?: string;
  replicateExisting?: boolean;
}

/** The `replication.rules` list for an ACK S3 bucket. */
export function replicationRules(input: ReplicationInput): Record<string, unknown>[] {
  return [
    {
      id: "cross-region",
      priority: 0,
      status: "Enabled",
      filter: { prefix: input.prefix ?? "" },
      deleteMarkerReplication: { status: "Enabled" },
      existingObjectReplication: {
        status: input.replicateExisting === true ? "Enabled" : "Disabled",
      },
      destination: {
        bucket: input.destinationBucketARN,
        storageClass: input.storageClass ?? "STANDARD_IA",
      },
    },
  ];
}
