/**
 * dev environment — clusters build root.
 *
 * `chant build src/envs/dev/clusters` sees this file only. The output lands
 * at `dist/dev/clusters/manifests.yaml`, the path `../delivery/main.ts`'s
 * `myapp-dev-clusters` Kustomization reconciles.
 */

import {
  RegionCluster,
} from "../../../composites/index.js";

const ENV = "dev";
const REGION = "us-east-1";

// ── Cluster ──────────────────────────────────────────────────────────────────
// SPEC: 2 nodes, t3.medium.

export const cluster = RegionCluster({
  name: "myapp-dev",
  env: ENV,
  region: REGION,
  availabilityZones: ["us-east-1a", "us-east-1b"],
  nodeCount: 2,
  instanceType: "t3.medium",
  publicEndpoint: true,
});
