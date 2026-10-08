/**
 * dev environment — infra build root.
 *
 * The ACK-reconciled cloud resources and the chart-delivered ACK controllers
 * that reconcile them. `chant build src/envs/dev/infra` sees this file only —
 * not `../clusters`, not `../delivery`. The output lands at
 * `dist/dev/infra/manifests.yaml`, the path `../delivery/main.ts`'s
 * `myapp-dev-infra` Kustomization reconciles. See README, "Build output
 * layout".
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

const ENV = "dev";
const REGION = "us-east-1";
const ACK_REPOSITORY = "aws-controllers-k8s";

// ── Application assets bucket ────────────────────────────────────────────────
// SPEC dev: versioned + encrypted. Replication is the prod arm.

export const assets = SecureBucket({
  name: "myapp-assets-dev",
  env: ENV,
  region: REGION,
});

// ── Database ─────────────────────────────────────────────────────────────────
// SPEC dev: db.t3.micro, single AZ.

/**
 * Referenced provenance. The Secret is created out of band — nothing in this
 * repo holds the value, and nothing in the build output does either.
 */
const masterPassword: SecretRef = {
  name: "myapp-dev-db-master",
  namespace: INFRA_NAMESPACE,
  key: "password",
  scope: "created by the platform runbook before the DBInstance reconciles",
};

export const database = PostgresInstance({
  name: "myapp-dev-db",
  env: ENV,
  instanceClass: "db.t3.micro",
  databaseName: "appdb",
  masterUsername: "appuser",
  masterPassword,
  dbSubnetGroupName: "myapp-dev-subnets",
  multiAZ: false,
  vpcSecurityGroupIDs: ["sg-dev-database"],
});

// ── Service-account identity ─────────────────────────────────────────────────
// SPEC dev: programmatic access.

export const reader = ReaderIam({
  name: "myapp-dev",
  env: ENV,
  bucketName: "myapp-assets-dev",
  trust: { mode: "account", accountID: "123456789012" },
  programmaticAccess: true,
});

// ── Chart-delivered controllers ──────────────────────────────────────────────
// The ACK controllers that reconcile the custom resources above are themselves
// chart-delivered — declared as typed Flux objects, not applied by hand.

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
});

export const rdsController = AckController({
  service: "rds",
  env: ENV,
  region: REGION,
  chartVersion: "1.4.14",
  repositoryName: ackCharts.name,
});

export const iamController = AckController({
  service: "iam",
  env: ENV,
  region: REGION,
  chartVersion: "1.3.16",
  repositoryName: ackCharts.name,
});
