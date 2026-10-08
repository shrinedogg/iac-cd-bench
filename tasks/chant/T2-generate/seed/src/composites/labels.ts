/**
 * Label and tag helpers.
 *
 * These are functions rather than constants because every label set is derived
 * from props. A composite factory calling one of these satisfies EVL009 (an
 * imported identifier is a legitimate reference) while keeping the label
 * vocabulary defined in exactly one place — the `clusterSelector` on the flux
 * addon and the labels on the CAPI `Cluster` are literally the same call.
 */

/** Label key carrying the environment identity across every resource kind. */
export const ENV_LABEL = "iac-cd-bench.dev/env";
/** Label key carrying the cluster identity. */
export const CLUSTER_LABEL = "cluster.x-k8s.io/cluster-name";
/** Label key carrying the AWS region. */
export const REGION_LABEL = "iac-cd-bench.dev/region";
/** Label key naming the logical component within the estate. */
export const COMPONENT_LABEL = "app.kubernetes.io/component";
/** Label key naming the estate itself. */
export const PART_OF_LABEL = "app.kubernetes.io/part-of";

/** The estate every resource in this golden belongs to. */
export const ESTATE = "iac-cd-bench";

/** Labels stamped on a cluster and matched by its addon `clusterSelector`. */
export function clusterLabels(
  clusterName: string,
  env: string,
  region: string,
): Record<string, string> {
  return {
    [CLUSTER_LABEL]: clusterName,
    [ENV_LABEL]: env,
    [REGION_LABEL]: region,
    [PART_OF_LABEL]: ESTATE,
  };
}

/** Labels stamped on a node pool and on the nodes it registers. */
export function poolLabels(clusterName: string, env: string): Record<string, string> {
  return {
    [CLUSTER_LABEL]: clusterName,
    [ENV_LABEL]: env,
    [PART_OF_LABEL]: ESTATE,
  };
}

/** Labels stamped on the ACK custom resources and the Flux objects. */
export function infraLabels(component: string, env: string): Record<string, string> {
  return {
    [COMPONENT_LABEL]: component,
    [ENV_LABEL]: env,
    [PART_OF_LABEL]: ESTATE,
  };
}

/** ACK tag-set entries (`[{ key, value }]`) for an S3 bucket / RDS instance. */
export function ackTags(component: string, env: string): { key: string; value: string }[] {
  return [
    { key: "Component", value: component },
    { key: "Environment", value: env },
    { key: "PartOf", value: ESTATE },
    { key: "ManagedBy", value: "chant" },
  ];
}
