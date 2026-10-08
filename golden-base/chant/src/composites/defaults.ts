/**
 * Constants shared by the scenario composites.
 *
 * EVL009 forbids object/array literals inside a `Composite()` factory that do
 * not reference props or sibling members — they belong here, imported by name.
 * Everything in this file is scenario policy: the posture the SPEC acceptance
 * criteria demand, expressed once instead of per resource.
 */

// ── API groups ───────────────────────────────────────────────────────────────
// CAPI v1beta2 refs carry `apiGroup` (not `apiVersion`, which is what the
// v1beta1-era knr-ops YAML uses).

export const CAPA_INFRA_GROUP = "infrastructure.cluster.x-k8s.io";
export const CAPA_CONTROLPLANE_GROUP = "controlplane.cluster.x-k8s.io";

export const AWS_MANAGED_CLUSTER_KIND = "AWSManagedCluster";
export const AWS_MANAGED_CONTROL_PLANE_KIND = "AWSManagedControlPlane";
export const AWS_MANAGED_MACHINE_POOL_KIND = "AWSManagedMachinePool";

// ── Namespaces ───────────────────────────────────────────────────────────────

/** Namespace the CAPI/CAPA objects for managed clusters live in. */
export const CLUSTERS_NAMESPACE = "clusters";
/** Namespace the ACK custom resources live in. */
export const INFRA_NAMESPACE = "infra";
/** Namespace the Flux controllers run in and watch by default. */
export const FLUX_NAMESPACE = "flux-system";

// ── Cluster defaults ─────────────────────────────────────────────────────────

export const DEFAULT_KUBERNETES_VERSION = "v1.31.2";
export const DEFAULT_POD_CIDR_BLOCKS = ["192.168.0.0/16"];
export const DEFAULT_SERVICE_CIDR_BLOCKS = ["10.96.0.0/12"];

/** Control-plane API endpoint posture: private reachable, public restricted. */
export const PRIVATE_ENDPOINT_ACCESS = { private: true, public: false };
export const PUBLIC_ENDPOINT_ACCESS = { private: true, public: true };

/** EKS control-plane log streams worth paying for on every cluster. */
export const CONTROL_PLANE_LOGGING = {
  apiServer: true,
  audit: true,
  authenticator: true,
  controllerManager: false,
  scheduler: false,
};

/** EKS access config — API-only auth, no implicit creator admin. */
export const CLUSTER_ACCESS_CONFIG = {
  authenticationMode: "api",
  bootstrapClusterCreatorAdminPermissions: false,
};

/** Managed node group AMI/capacity posture. */
export const DEFAULT_AMI_TYPE = "AL2023_x86_64_STANDARD";
export const DEFAULT_CAPACITY_TYPE = "onDemand";
export const DEFAULT_UPDATE_CONFIG = { maxUnavailable: 1 };
export const DEFAULT_DISK_SIZE_GIB = 50;

// ── Flux addon (CAAPH) ───────────────────────────────────────────────────────

export const FLUX_CHART_REPO_URL = "https://fluxcd-community.github.io/helm-charts";
export const FLUX_CHART_NAME = "flux2";
export const FLUX_CHART_VERSION = "2.14.0";
export const FLUX_ADDON_OPTIONS = {
  wait: true,
  waitForJobs: true,
  install: { createNamespace: true, includeCRDs: true },
};

// ── ACK controllers (chart-delivered) ────────────────────────────────────────

/** Namespace the AWS Controllers for Kubernetes run in. */
export const ACK_NAMESPACE = "ack-system";
/** OCI registry hosting the ACK controller charts. */
export const ACK_CHART_REGISTRY = "oci://public.ecr.aws/aws-controllers-k8s";
/** Install policy: create the namespace and the CRDs the controller owns. */
export const ACK_CRD_POLICY = { createNamespace: true, crds: "Create" };
/** Upgrade policy: replace CRDs so field additions land without manual apply. */
export const ACK_UPGRADE_POLICY = { crds: "CreateReplace" };

// ── S3 posture (SPEC acceptance criterion 1) ─────────────────────────────────

export const VERSIONING_ENABLED = { status: "Enabled" };

/** Block every route to public access. Non-negotiable in both environments. */
export const PUBLIC_ACCESS_BLOCKED = {
  blockPublicACLs: true,
  blockPublicPolicy: true,
  ignorePublicACLs: true,
  restrictPublicBuckets: true,
};

export const AES256_ENCRYPTION = {
  rules: [{ applyServerSideEncryptionByDefault: { sseAlgorithm: "AES256" }, bucketKeyEnabled: true }],
};

/** Ownership must be bucket-owner-enforced for ACLs to be irrelevant. */
export const BUCKET_OWNER_ENFORCED = { rules: [{ objectOwnership: "BucketOwnerEnforced" }] };

// ── RDS posture (SPEC acceptance criterion 2) ────────────────────────────────

export const POSTGRES_ENGINE = "postgres";
export const DEFAULT_POSTGRES_VERSION = "16.4";
export const DEFAULT_POSTGRES_PORT = 5432;
/** SPEC: backup retention >= 7 days. */
export const MINIMUM_BACKUP_RETENTION_DAYS = 7;
export const DEFAULT_BACKUP_WINDOW = "03:00-04:00";
export const DEFAULT_MAINTENANCE_WINDOW = "sun:04:30-sun:05:30";
export const POSTGRES_LOG_EXPORTS = ["postgresql", "upgrade"];

// ── IAM posture (SPEC acceptance criterion 4) ────────────────────────────────

/** Read-only S3 actions. Enumerated — a wildcard here fails prod review. */
export const S3_READER_ACTIONS = ["s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket"];
export const DEFAULT_MAX_SESSION_DURATION_SECONDS = 3600;
