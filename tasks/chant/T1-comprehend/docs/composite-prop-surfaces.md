# Composite prop surfaces

The scenario-local `Composite()` factories this golden is built from, and
what each one emits. Required props first, then optional. Source: `src/composites/`.

## `RegionCluster` — `src/composites/region-cluster.ts`

Members: `cluster` (`K8s::CAPI::Cluster`), `infra` (`AWSManagedCluster`),
`controlPlane` (`AWSManagedControlPlane`), `nodePool` (a nested
`RegionNodePool`: `awsPool` + `machinePool`), `fluxAddon` (`HelmChartProxy`).

| Prop | Type | Default |
|---|---|---|
| `name` | `string` | — |
| `env` | `string` | — |
| `region` | `string` | — |
| `availabilityZones` | `string[]` | — |
| `nodeCount` | `number` | — |
| `instanceType` | `string` | — |
| `minNodeCount` | `number` | `nodeCount` |
| `maxNodeCount` | `number` | `nodeCount` |
| `version` | `string` | `v1.31.2` |
| `publicEndpoint` | `boolean` | `false` (private-only API endpoint) |
| `associateOIDCProvider` | `boolean` | `true` |
| `fluxChartVersion` | `string` | `2.14.0` |

dev: 2 nodes, t3.medium, public endpoint. prod: 4 nodes (min 4 / max 8),
t3.large, private endpoint. Six resource kinds per cluster
(`cluster`/`infra`/`controlPlane`/`fluxAddon` are one each; `nodePool`
expands to two: `AWSManagedMachinePool` + `MachinePool`).

## `SecureBucket` — `src/composites/secure-bucket.ts`

Members: `bucket` (`K8s::S3::Bucket`), `replicaRole` (`K8s::Iam::Role`,
**only when `replication` is set**).

| Prop | Type | Default |
|---|---|---|
| `name` | `string` | — |
| `env` | `string` | — |
| `region` | `string` | — |
| `kmsKeyARN` | `string` | unset → SSE-S3 (AES-256) |
| `replication` | `{ destinationBucketARN, storageClass?, prefix?, replicateExisting? }` | unset |

Versioning, encryption, public-access block, and bucket-owner-enforced
ownership are unconditional — no prop turns any of them off. dev's `assets`
bucket has no `replication` prop, so it produces one resource (`bucket`
only). prod's `assets` bucket sets `replication`, so it produces two
(`bucket` + `replicaRole`); prod also declares a *second*, independent
`SecureBucket` call (`assetsReplica`) for the destination bucket itself,
which has no `replication` prop of its own.

## `PostgresInstance` — `src/composites/postgres-instance.ts`

Members: `instance` (`K8s::Rds::DBInstance`) only.

| Prop | Type | Default |
|---|---|---|
| `name` / `env` / `instanceClass` / `databaseName` / `masterUsername` / `masterPassword` / `dbSubnetGroupName` | required | — |
| `allocatedStorageGiB` | `number` | `20` |
| `multiAZ` | `boolean` | `false` |
| `storageEncrypted` | `boolean` | `true` |
| `backupRetentionDays` | `number` | `7`; **below 7 throws at build time** |

`masterPassword` is a `SecretRef` (`{ name, namespace, key, scope? }`) — a
pointer at a Kubernetes Secret that exists out of band, never a value.
`deletionProtection: true` and `publiclyAccessible: false` are pinned in
the factory body, not props — there is no prop that can weaken either one
through this composite.

## `ReaderIam` — `src/composites/reader-iam.ts`

Members: `policy` (`K8s::Iam::Policy`), `role` (`K8s::Iam::Role`), `user`
(`K8s::Iam::User`, **only when `programmaticAccess` is `true`**),
`podIdentity` (`K8s::Eks::PodIdentityAssociation`, **only when the
`podIdentity` prop is set**).

| Prop | Type | Default |
|---|---|---|
| `name` / `env` / `bucketName` / `trust` | required | — |
| `additionalActions` | `string[]` | unset (enumerated only — no wildcard) |
| `programmaticAccess` | `boolean` | `false` |
| `podIdentity` | `{ clusterName, serviceAccountNamespace, serviceAccountName }` | unset |

dev's `reader` call: `trust: { mode: "account" }`, `programmaticAccess:
true`, no `podIdentity` — one `Policy`, one `Role`, one `User`, no
`PodIdentityAssociation`. prod's `reader` call: `trust: { mode: "oidc" }`,
`programmaticAccess: false`, `podIdentity` set — one `Policy`, one `Role`,
no `User`, one `PodIdentityAssociation`.

## `AckController` — `src/composites/ack-controller.ts`

Members: `release` (`K8s::Flux::HelmRelease`) only.

| Prop | Type | Default |
|---|---|---|
| `service` (`s3`/`rds`/`iam`) / `env` / `region` / `chartVersion` / `repositoryName` | required | — |
| `replicas` | `number` | `1` |

Every environment's `infra` build root calls this once per ACK service
(`s3Controller`, `rdsController`, `iamController`), all three sharing the
one `HelmRepository` (`ackCharts`) that build root also declares. prod's
three calls set `replicas: 2`; dev's leave it at the default `1`. The
`HelmRelease`'s `spec.chart.spec.sourceRef.name` is `props.repositoryName`
— literally `ackCharts.name` at every dev and prod call site, which is the
reference the declared graph resolves into an edge (see
`graph-ir-dev-declared.json`).

## Secrets: referenced provenance

`src/composites/secrets.ts`'s `SecretRef` has no field that can hold a
value — only `name`, `namespace`, `key`, and an optional free-text `scope`
describing where the real value lives out of band. `secretRef(ref)`
projects it to the `{ name, namespace, key }` shape ACK's
`SecretKeyReference` expects. `PostgresInstance` is the only composite that
consumes a `SecretRef` today, via `masterPassword` → `spec.masterUserPassword:
secretRef(props.masterPassword)`.
