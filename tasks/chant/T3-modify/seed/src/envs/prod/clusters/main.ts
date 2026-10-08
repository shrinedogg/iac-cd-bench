/**
 * prod environment — clusters build root.
 *
 * `chant build src/envs/prod/clusters` sees this file only. The output lands
 * at `dist/prod/clusters/manifests.yaml`, the path `../delivery/main.ts`'s
 * `myapp-prod-clusters` Kustomization reconciles.
 */

import {
  RegionCluster,
} from "../../../composites/index.js";

const ENV = "prod";
const REGION = "us-east-1";

// ── Cluster ──────────────────────────────────────────────────────────────────
// SPEC: 4 nodes, t3.large. Private API endpoint; IRSA/OIDC provider on.

export const cluster = RegionCluster({
  name: "myapp-prod",
  env: ENV,
  region: REGION,
  availabilityZones: ["us-east-1a", "us-east-1b", "us-east-1c"],
  nodeCount: 4,
  instanceType: "t3.large",
  minNodeCount: 4,
  maxNodeCount: 8,
  publicEndpoint: false,
  associateOIDCProvider: true,
});
