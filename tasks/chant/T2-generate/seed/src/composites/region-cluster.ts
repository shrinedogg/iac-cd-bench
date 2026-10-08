/**
 * RegionCluster — one EKS cluster in one region, declared once.
 *
 * This is the composite the benchmark thesis rests on. The knr-ops idiom needs
 * a `clusters/<region>/` directory per cluster holding four hand-written
 * manifests (Cluster, control plane, machine deployment, machine template)
 * whose bodies differ by a handful of lines — region, AZs, instance type,
 * replica count. Here those four objects come out of one call, and the lines
 * that actually differ are the props.
 *
 * Members:
 *   cluster       K8s::CAPI::Cluster
 *   infra         K8s::Infrastructure::AWSManagedCluster
 *   controlPlane  K8s::Controlplane::AWSManagedControlPlane
 *   nodePool      RegionNodePool (MachinePool + AWSManagedMachinePool)
 *   fluxAddon     K8s::Addons::HelmChartProxy   (CAAPH-delivered flux2 chart)
 *
 * Additional node pools are extra `RegionNodePool({ clusterName: ... })` calls
 * at the call site rather than an array prop: a composite factory returns a
 * flat record of named members (core's `CompositeMembers`), and iterating a
 * pool array inside the factory is exactly what EVL010 exists to prevent.
 * One pool per cluster is what the SPEC asks for; the second pool stays a
 * one-liner.
 */

import { Composite } from "@intentius/chant";
import {
  AWSManagedCluster,
  AWSManagedControlPlane,
  AWSManagedMachinePool,
  CAPICluster,
  HelmChartProxy,
  MachinePool,
} from "@intentius/chant-lexicon-k8s";

import {
  AWS_MANAGED_CLUSTER_KIND,
  AWS_MANAGED_CONTROL_PLANE_KIND,
  AWS_MANAGED_MACHINE_POOL_KIND,
  CAPA_CONTROLPLANE_GROUP,
  CAPA_INFRA_GROUP,
  CLUSTERS_NAMESPACE,
  CONTROL_PLANE_LOGGING,
  CLUSTER_ACCESS_CONFIG,
  DEFAULT_AMI_TYPE,
  DEFAULT_CAPACITY_TYPE,
  DEFAULT_DISK_SIZE_GIB,
  DEFAULT_KUBERNETES_VERSION,
  DEFAULT_POD_CIDR_BLOCKS,
  DEFAULT_SERVICE_CIDR_BLOCKS,
  DEFAULT_UPDATE_CONFIG,
  FLUX_ADDON_OPTIONS,
  FLUX_CHART_NAME,
  FLUX_CHART_REPO_URL,
  FLUX_CHART_VERSION,
  FLUX_NAMESPACE,
  PRIVATE_ENDPOINT_ACCESS,
  PUBLIC_ENDPOINT_ACCESS,
} from "./defaults.js";
import { clusterLabels, poolLabels } from "./labels.js";

// ── RegionNodePool ───────────────────────────────────────────────────────────

export interface RegionNodePoolProps {
  /** Node-pool name; also the EKS managed node group name. */
  name: string;
  /** CAPI cluster this pool joins. */
  clusterName: string;
  /** Environment identity, stamped as a label on the pool's nodes. */
  env: string;
  /** EC2 instance type (SPEC: t3.medium in dev, t3.large in prod). */
  instanceType: string;
  /** Desired node count. */
  replicas: number;
  /** Autoscaling floor. Defaults to `replicas` when omitted. */
  minSize?: number;
  /** Autoscaling ceiling. Defaults to `replicas` when omitted. */
  maxSize?: number;
  /** AZs the node group spreads across. */
  availabilityZones: string[];
  /** Kubernetes version; must track the control plane. */
  version?: string;
  /** EKS AMI family. */
  amiType?: string;
  /** `onDemand` or `spot`. */
  capacityType?: string;
  /** Root volume size in GiB. */
  diskSizeGiB?: number;
  /** Namespace the CAPI objects live in. */
  namespace?: string;
}

export const RegionNodePool = Composite<RegionNodePoolProps>((props) => {
  const awsPool = new AWSManagedMachinePool({
    metadata: {
      name: props.name,
      namespace: props.namespace ?? CLUSTERS_NAMESPACE,
      labels: poolLabels(props.clusterName, props.env),
    },
    spec: {
      eksNodegroupName: props.name,
      instanceType: props.instanceType,
      availabilityZones: props.availabilityZones,
      amiType: props.amiType ?? DEFAULT_AMI_TYPE,
      capacityType: props.capacityType ?? DEFAULT_CAPACITY_TYPE,
      diskSize: props.diskSizeGiB ?? DEFAULT_DISK_SIZE_GIB,
      scaling: {
        minSize: props.minSize ?? props.replicas,
        maxSize: props.maxSize ?? props.replicas,
      },
      updateConfig: DEFAULT_UPDATE_CONFIG,
      labels: poolLabels(props.clusterName, props.env),
    },
  });

  const machinePool = new MachinePool({
    metadata: {
      name: props.name,
      namespace: props.namespace ?? CLUSTERS_NAMESPACE,
      labels: poolLabels(props.clusterName, props.env),
    },
    spec: {
      clusterName: props.clusterName,
      replicas: props.replicas,
      template: {
        spec: {
          clusterName: props.clusterName,
          version: props.version ?? DEFAULT_KUBERNETES_VERSION,
          // EKS managed node groups get their bootstrap data from EKS, not
          // from a CAPI bootstrap provider. An empty dataSecretName is how
          // CAPA says "nothing to do here" while satisfying the required
          // `bootstrap` field.
          bootstrap: { dataSecretName: "" },
          infrastructureRef: {
            apiGroup: CAPA_INFRA_GROUP,
            kind: AWS_MANAGED_MACHINE_POOL_KIND,
            name: props.name,
          },
        },
      },
    },
  });

  return { awsPool, machinePool };
}, "RegionNodePool");

// ── RegionCluster ────────────────────────────────────────────────────────────

export interface RegionClusterProps {
  /** Cluster name. Every member's name derives from it. */
  name: string;
  /** Environment identity (`dev` / `prod`), stamped as a label. */
  env: string;
  /** AWS region the control plane and nodes live in. */
  region: string;
  /** AZs the managed node group spreads across. */
  availabilityZones: string[];
  /** Desired node count (SPEC: 2 in dev, 4 in prod). */
  nodeCount: number;
  /** EC2 instance type (SPEC: t3.medium in dev, t3.large in prod). */
  instanceType: string;
  /** Autoscaling floor for the default pool. Defaults to `nodeCount`. */
  minNodeCount?: number;
  /** Autoscaling ceiling for the default pool. Defaults to `nodeCount`. */
  maxNodeCount?: number;
  /** Kubernetes version for control plane and nodes. */
  version?: string;
  /** Expose the EKS API endpoint publicly. Default false (private only). */
  publicEndpoint?: boolean;
  /** Mint an IAM OIDC provider for the cluster, enabling IRSA. Default true. */
  associateOIDCProvider?: boolean;
  /** flux2 chart version CAAPH installs into the workload cluster. */
  fluxChartVersion?: string;
  /** Namespace the CAPI/CAPA objects live in. */
  namespace?: string;
  /** Tags applied to every AWS resource CAPA creates for this cluster. */
  additionalTags?: Record<string, string>;
}

/**
 * The member record has to be named explicitly rather than inferred: `nodePool`
 * is a nested `CompositeInstance`, which core's default `CompositeMembers`
 * (a `Record<string, Declarable>`) does not admit. `CompositeFactoryMembers`
 * does, and naming the type is how the factory opts into it.
 */
export type RegionClusterMembers = {
  cluster: InstanceType<typeof CAPICluster>;
  infra: InstanceType<typeof AWSManagedCluster>;
  controlPlane: InstanceType<typeof AWSManagedControlPlane>;
  nodePool: ReturnType<typeof RegionNodePool>;
  fluxAddon: InstanceType<typeof HelmChartProxy>;
};

export const RegionCluster = Composite<RegionClusterProps, RegionClusterMembers>((props) => {
  const controlPlane = new AWSManagedControlPlane({
    metadata: {
      name: `${props.name}-control-plane`,
      namespace: props.namespace ?? CLUSTERS_NAMESPACE,
      labels: clusterLabels(props.name, props.env, props.region),
    },
    spec: {
      eksClusterName: props.name,
      region: props.region,
      version: props.version ?? DEFAULT_KUBERNETES_VERSION,
      accessConfig: CLUSTER_ACCESS_CONFIG,
      endpointAccess: props.publicEndpoint === true
        ? PUBLIC_ENDPOINT_ACCESS
        : PRIVATE_ENDPOINT_ACCESS,
      logging: CONTROL_PLANE_LOGGING,
      // IRSA: the OIDC provider the prod ReaderIam role trusts.
      associateOIDCProvider: props.associateOIDCProvider ?? true,
      additionalTags: props.additionalTags,
    },
  });

  const infra = new AWSManagedCluster({
    metadata: {
      name: props.name,
      namespace: props.namespace ?? CLUSTERS_NAMESPACE,
      labels: clusterLabels(props.name, props.env, props.region),
    },
    // AWSManagedCluster carries no desired state of its own — the EKS control
    // plane owns it all. Its whole job is to be the `infrastructureRef` target
    // that lets CAPI treat the managed control plane as the infrastructure.
  });

  const cluster = new CAPICluster({
    metadata: {
      name: props.name,
      namespace: props.namespace ?? CLUSTERS_NAMESPACE,
      // The addon HelmChartProxy selects on these labels — the cluster's
      // labels are the wiring, not a decoration.
      labels: clusterLabels(props.name, props.env, props.region),
    },
    spec: {
      clusterNetwork: {
        pods: { cidrBlocks: DEFAULT_POD_CIDR_BLOCKS },
        services: { cidrBlocks: DEFAULT_SERVICE_CIDR_BLOCKS },
      },
      infrastructureRef: {
        apiGroup: CAPA_INFRA_GROUP,
        kind: AWS_MANAGED_CLUSTER_KIND,
        name: props.name,
      },
      controlPlaneRef: {
        apiGroup: CAPA_CONTROLPLANE_GROUP,
        kind: AWS_MANAGED_CONTROL_PLANE_KIND,
        name: `${props.name}-control-plane`,
      },
    },
  });

  const nodePool = RegionNodePool({
    name: `${props.name}-nodes`,
    clusterName: props.name,
    env: props.env,
    instanceType: props.instanceType,
    replicas: props.nodeCount,
    minSize: props.minNodeCount,
    maxSize: props.maxNodeCount,
    availabilityZones: props.availabilityZones,
    version: props.version,
    namespace: props.namespace,
  });

  // Flux itself is chart-delivered into the workload cluster by the Cluster
  // API Add-on Provider for Helm, selected by the cluster labels above. This
  // is the bootstrap edge: everything after it is reconciled by the Flux
  // objects the delivery module declares.
  const fluxAddon = new HelmChartProxy({
    metadata: {
      name: `${props.name}-flux`,
      namespace: props.namespace ?? CLUSTERS_NAMESPACE,
      labels: clusterLabels(props.name, props.env, props.region),
    },
    spec: {
      clusterSelector: { matchLabels: clusterLabels(props.name, props.env, props.region) },
      repoURL: FLUX_CHART_REPO_URL,
      chartName: FLUX_CHART_NAME,
      version: props.fluxChartVersion ?? FLUX_CHART_VERSION,
      releaseName: FLUX_CHART_NAME,
      namespace: FLUX_NAMESPACE,
      reconcileStrategy: "Continuous",
      options: FLUX_ADDON_OPTIONS,
    },
  });

  return { cluster, infra, controlPlane, nodePool, fluxAddon };
}, "RegionCluster");
