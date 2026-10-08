/**
 * prod environment — delivery build root.
 *
 * The Flux objects that reconcile `../infra` and `../clusters` from git. See
 * `../../dev/delivery/main.ts` and README, "Build output layout".
 */

import { FluxAppFor, FluxGitSource } from "@intentius/chant-lexicon-k8s";

import { INFRA_NAMESPACE } from "../../../composites/index.js";

export const source = FluxGitSource("myapp-infra", {
  url: "https://github.com/example/myapp-infra",
  branch: "main",
  interval: "1m",
});

export const infraApp = FluxAppFor("myapp-prod-infra", {
  source,
  path: "./dist/prod/infra",
  targetNamespace: INFRA_NAMESPACE,
  interval: "10m",
});

export const clusterApp = FluxAppFor("myapp-prod-clusters", {
  source,
  path: "./dist/prod/clusters",
  interval: "10m",
  dependsOn: ["myapp-prod-infra"],
});
