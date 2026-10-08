/**
 * AckController — one AWS Controllers for Kubernetes service controller,
 * chart-delivered by Flux's helm-controller.
 *
 * The ACK controllers are what actually reconcile the `K8s::S3::Bucket`,
 * `K8s::Rds::DBInstance`, and `K8s::Iam::*` custom resources the other
 * composites declare. They arrive as Helm charts, so they are declared as a
 * typed `K8s::Flux::HelmRelease` — the knr-ops idiom's `HelmRelease`, with the
 * fields typed and the repeated 20-line body collapsed into a call.
 *
 * This is a fifth scenario-local composite beyond the four in epic #2. It
 * earns its place on the same argument the other four do: without it each
 * environment repeats three near-identical HelmRelease bodies, which is the
 * duplication this arm exists to remove.
 *
 * Members:
 *   release  K8s::Flux::HelmRelease
 */

import { Composite } from "@intentius/chant";
import { HelmRelease } from "@intentius/chant-lexicon-k8s";

import { ACK_CRD_POLICY, ACK_NAMESPACE, ACK_UPGRADE_POLICY, FLUX_NAMESPACE } from "./defaults.js";
import { infraLabels } from "./labels.js";

export interface AckControllerProps {
  /** ACK service short name, e.g. `s3`, `rds`, `iam`. */
  service: string;
  /** Environment identity, stamped as a label. */
  env: string;
  /** AWS region the controller reconciles into. */
  region: string;
  /** Pinned chart version. An unpinned controller is an unreviewable upgrade. */
  chartVersion: string;
  /** Name of the `HelmRepository` the chart comes from. */
  repositoryName: string;
  /** Reconcile interval. */
  interval?: string;
  /** Controllers that must be ready first (`spec.dependsOn`). */
  dependsOn?: { name: string; namespace?: string }[];
  /** Replica count for the controller deployment. */
  replicas?: number;
  /** Namespace the controllers are installed into. */
  targetNamespace?: string;
}

export const AckController = Composite<AckControllerProps>((props) => {
  const release = new HelmRelease({
    metadata: {
      name: `ack-${props.service}-controller`,
      namespace: FLUX_NAMESPACE,
      labels: infraLabels("ack", props.env),
    },
    spec: {
      interval: props.interval ?? "10m",
      releaseName: `ack-${props.service}-controller`,
      targetNamespace: props.targetNamespace ?? ACK_NAMESPACE,
      storageNamespace: props.targetNamespace ?? ACK_NAMESPACE,
      install: ACK_CRD_POLICY,
      upgrade: ACK_UPGRADE_POLICY,
      dependsOn: props.dependsOn,
      chart: {
        spec: {
          chart: `${props.service}-chart`,
          version: props.chartVersion,
          sourceRef: {
            kind: "HelmRepository",
            name: props.repositoryName,
            namespace: FLUX_NAMESPACE,
          },
        },
      },
      values: {
        aws: { region: props.region },
        deployment: { replicas: props.replicas ?? 1 },
      },
    },
  });

  return { release };
}, "AckController");
