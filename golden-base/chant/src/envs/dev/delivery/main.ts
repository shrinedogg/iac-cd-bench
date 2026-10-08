/**
 * dev environment — delivery build root.
 *
 * The Flux objects that reconcile `../infra` and `../clusters` from git: one
 * `GitRepository` source, one `Kustomization` per reconciled path — the
 * lexicon's tested Flux composites, not hand-rolled ones.
 *
 * This build root is the bootstrap edge: its output (`dist/dev/delivery.yaml`)
 * is what a `flux bootstrap`-style apply installs directly onto the
 * management cluster, the same way flux-system's own `gotk-sync.yaml` is.
 * Nothing in `../infra` or `../clusters` reconciles *this* path — that would
 * be circular — so it carries no `FluxAppFor` path of its own. See README,
 * "Build output layout".
 */

import { FluxAppFor, FluxGitSource } from "@intentius/chant-lexicon-k8s";

import { INFRA_NAMESPACE } from "../../../composites/index.js";

export const source = FluxGitSource("myapp-infra", {
  url: "https://github.com/example/myapp-infra",
  branch: "main",
  interval: "1m",
});

export const infraApp = FluxAppFor("myapp-dev-infra", {
  source,
  path: "./dist/dev/infra",
  targetNamespace: INFRA_NAMESPACE,
  interval: "10m",
});

export const clusterApp = FluxAppFor("myapp-dev-clusters", {
  source,
  path: "./dist/dev/clusters",
  interval: "10m",
  dependsOn: ["myapp-dev-infra"],
});
