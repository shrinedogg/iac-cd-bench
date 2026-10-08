/**
 * prod environment — infra build root.
 *
 * Read this next to `../../dev/infra/main.ts`. Same shape, differing only
 * where the SPEC matrix says: a replica bucket, cross-region replication, a
 * multi-AZ encrypted database, and an OIDC-trusted role instead of an IAM
 * user. `chant build src/envs/prod/infra` sees this file only. The output
 * lands at `dist/prod/infra/manifests.yaml`, the path
 * `../delivery/main.ts`'s `myapp-prod-infra` Kustomization reconciles.
 */

import { HelmRepository } from "@intentius/chant-lexicon-k8s";

import {
  ACK_CHART_REGISTRY,
  AckController,
  FLUX_NAMESPACE,
  INFRA_NAMESPACE,
  PostgresInstance,
  ReaderIam,
  SecureBucket,
  infraLabels,
  type SecretRef,
} from "../../../composites/index.js";

const ENV = "prod";
const REGION = "us-east-1";
const REPLICA_REGION = "us-west-2";
const ACK_REPOSITORY = "aws-controllers-k8s";
const APP_NAMESPACE = "app";
const SERVICE_ACCOUNT = "myapp";

// ── Application assets bucket ────────────────────────────────────────────────
// SPEC prod: versioned + encrypted + cross-region replication to us-west-2.

export const assetsReplica = SecureBucket({
  name: "myapp-assets-prod-replica",
  env: ENV,
  region: REPLICA_REGION,
  component: "assets-replica",
});

export const assets = SecureBucket({
  name: "myapp-assets-prod",
  env: ENV,
  region: REGION,
  replication: {
    destinationBucketARN: "arn:aws:s3:::myapp-assets-prod-replica",
    storageClass: "STANDARD_IA",
    replicateExisting: true,
  },
});

// ── Database ─────────────────────────────────────────────────────────────────
// SPEC prod: db.t3.medium, multi-AZ, encrypted.

/** Referenced provenance — see src/composites/secrets.ts. */
const masterPassword: SecretRef = {
  name: "myapp-prod-db-master",
  namespace: INFRA_NAMESPACE,
  key: "password",
  scope: "rotated into the cluster by the platform runbook; never in git",
};

export const database = PostgresInstance({
  name: "myapp-prod-db",
  env: ENV,
  instanceClass: "db.t3.medium",
  databaseName: "appdb",
  masterUsername: "appuser",
  masterPassword,
  dbSubnetGroupName: "myapp-prod-subnets",
  allocatedStorageGiB: 100,
  maxAllocatedStorageGiB: 500,
  multiAZ: true,
  storageEncrypted: true,
  backupRetentionDays: 5,
  vpcSecurityGroupIDs: ["sg-prod-database"],
});

// ── Service-account identity ─────────────────────────────────────────────────
// SPEC prod: least-privilege role, OIDC trust, no wildcards, no IAM user.

export const reader = ReaderIam({
  name: "myapp-prod",
  env: ENV,
  bucketName: "myapp-assets-prod",
  trust: {
    mode: "oidc",
    providerARN: "arn:aws:iam::123456789012:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/EXAMPLEPROD",
    issuerHost: "oidc.eks.us-east-1.amazonaws.com/id/EXAMPLEPROD",
    serviceAccountNamespace: APP_NAMESPACE,
    serviceAccountName: SERVICE_ACCOUNT,
  },
  programmaticAccess: false,
  podIdentity: {
    clusterName: "myapp-prod",
    serviceAccountNamespace: APP_NAMESPACE,
    serviceAccountName: SERVICE_ACCOUNT,
  },
});

// ── Chart-delivered controllers ──────────────────────────────────────────────

const ackLabels = infraLabels("ack", ENV);

export const ackCharts = new HelmRepository({
  metadata: {
    name: ACK_REPOSITORY,
    namespace: FLUX_NAMESPACE,
    labels: ackLabels,
  },
  spec: {
    type: "oci",
    url: ACK_CHART_REGISTRY,
    interval: "1h",
  },
});

export const s3Controller = AckController({
  service: "s3",
  env: ENV,
  region: REGION,
  chartVersion: "1.0.30",
  repositoryName: ackCharts.name,
  replicas: 2,
});

export const rdsController = AckController({
  service: "rds",
  env: ENV,
  region: REGION,
  chartVersion: "1.4.14",
  repositoryName: ackCharts.name,
  replicas: 2,
});

export const iamController = AckController({
  service: "iam",
  env: ENV,
  region: REGION,
  chartVersion: "1.3.16",
  repositoryName: ackCharts.name,
  replicas: 2,
});
