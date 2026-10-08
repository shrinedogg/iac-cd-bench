## Task: Review a chant diff for security issues

**Stack:** chant (TypeScript composites compiling to Flux + CAPI/CAPA + ACK Kubernetes manifests — no rendering pipeline beyond `chant build`, no policy engine, no CI gate beyond `chant lint` and whatever the reviewer catches by eye)

You are given a PR diff against `src/envs/prod/infra/main.ts` in a chant
golden repo. The file previously declared the prod database via the
`PostgresInstance` composite (`src/composites/postgres-instance.ts`), which
pins `deletionProtection: true` and `publiclyAccessible: false`
unconditionally in its factory body — neither is a prop a caller can set —
and only ever accepts the master password as a `SecretRef` (a pointer at a
Kubernetes Secret that exists out of band), never a literal value.

### PR Diff Preview

```diff
--- a/src/envs/prod/infra/main.ts
+++ b/src/envs/prod/infra/main.ts
 import { HelmRepository } from "@intentius/chant-lexicon-k8s";
+import { DBInstance } from "@intentius/chant-lexicon-k8s";

 import {
   ACK_CHART_REGISTRY,
   AckController,
   FLUX_NAMESPACE,
   INFRA_NAMESPACE,
-  PostgresInstance,
   ReaderIam,
   SecureBucket,
   infraLabels,
-  type SecretRef,
 } from "../../../composites/index.js";

 // ── Database ─────────────────────────────────────────────────────────────────
 // SPEC prod: db.t3.medium, multi-AZ, encrypted.

-/** Referenced provenance — see src/composites/secrets.ts. */
-const masterPassword: SecretRef = {
-  name: "myapp-prod-db-master",
-  namespace: INFRA_NAMESPACE,
-  key: "password",
-  scope: "rotated into the cluster by the platform runbook; never in git",
-};
-
-export const database = PostgresInstance({
-  name: "myapp-prod-db",
-  env: ENV,
-  instanceClass: "db.t3.medium",
-  databaseName: "appdb",
-  masterUsername: "appuser",
-  masterPassword,
-  dbSubnetGroupName: "myapp-prod-subnets",
-  allocatedStorageGiB: 100,
-  maxAllocatedStorageGiB: 500,
-  multiAZ: true,
-  storageEncrypted: true,
-  backupRetentionDays: 30,
-  vpcSecurityGroupIDs: ["sg-prod-database"],
-});
+// Switched to a raw DBInstance to unblock a hotfix without waiting on a
+// PostgresInstance composite change — TODO: move back once there's time.
+export const database = new DBInstance({
+  metadata: {
+    name: "myapp-prod-db",
+    namespace: INFRA_NAMESPACE,
+    labels: infraLabels("database", ENV),
+  },
+  spec: {
+    dbInstanceIdentifier: "myapp-prod-db",
+    dbInstanceClass: "db.t3.medium",
+    engine: "postgres",
+    engineVersion: "16.4",
+    dbName: "appdb",
+    port: 5432,
+    allocatedStorage: 100,
+    maxAllocatedStorage: 500,
+    masterUsername: "appuser",
+    masterUserPassword: "Prod2026!DbPass",
+    multiAZ: true,
+    storageEncrypted: true,
+    deletionProtection: false,
+    publiclyAccessible: true,
+    backupRetentionPeriod: 30,
+    dbSubnetGroupName: "myapp-prod-subnets",
+    vpcSecurityGroupIDs: ["sg-prod-database"],
+  },
+});

 // ── Service-account identity ─────────────────────────────────────────────────
 // SPEC prod: least-privilege role, OIDC trust, no wildcards, no IAM user.

 export const reader = ReaderIam({
   name: "myapp-prod",
   env: ENV,
   bucketName: "myapp-assets-prod",
   trust: {
     mode: "oidc",
     providerARN: "arn:aws:iam::123456789012:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/EXAMPLEPROD",
     issuerHost: "oidc.eks.us-east-1.amazonaws.com/id/EXAMPLEPROD",
     serviceAccountNamespace: APP_NAMESPACE,
     serviceAccountName: SERVICE_ACCOUNT,
   },
   programmaticAccess: false,
+  additionalActions: ["s3:*"],
   podIdentity: {
     clusterName: "myapp-prod",
     serviceAccountNamespace: APP_NAMESPACE,
     serviceAccountName: SERVICE_ACCOUNT,
   },
 });
```

### Your Task

Review this diff and identify security issues. For each one:
1. What is the issue, and which field/line introduces it?
2. Is it CRITICAL, HIGH, MEDIUM, or LOW severity?
3. What should be changed before this PR merges?

Then rank all the issues you found by severity, worst first.

### Context Files

{{scenario_spec}}
