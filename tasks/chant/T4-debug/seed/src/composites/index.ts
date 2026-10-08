/**
 * Scenario-local composites for the chant golden.
 *
 * These are deliberately *not* upstreamed to the k8s lexicon. They encode the
 * benchmark scenario's posture (scenario/SPEC.md), which is narrower than
 * anything a general-purpose lexicon should assert. Promotion is a later
 * decision, and only if the bench shows they earn it (epic #2).
 */

export { AckController } from "./ack-controller.js";
export type { AckControllerProps } from "./ack-controller.js";

export { RegionCluster, RegionNodePool } from "./region-cluster.js";
export type { RegionClusterProps, RegionNodePoolProps } from "./region-cluster.js";

export { SecureBucket } from "./secure-bucket.js";
export type { SecureBucketProps, SecureBucketReplication } from "./secure-bucket.js";

export { PostgresInstance } from "./postgres-instance.js";
export type { PostgresInstanceProps } from "./postgres-instance.js";

export { ReaderIam } from "./reader-iam.js";
export type { ReaderIamProps } from "./reader-iam.js";

export { assumeRolePolicy, readerPolicyDocument } from "./policies.js";
export type { AccountTrust, OidcTrust, RoleTrust, TrustMode } from "./policies.js";

export { describeSecret, fluxSecretRef, secretRef } from "./secrets.js";
export type { SecretRef } from "./secrets.js";

export * from "./labels.js";
export * from "./defaults.js";
