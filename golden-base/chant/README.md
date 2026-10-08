# golden-base/chant

The chant arm of the benchmark: `scenario/SPEC.md` written as typed TypeScript
that compiles to Kubernetes manifests, in the same Flux + CAPI + ACK idiom the
`golden-base/knr-ops` column hand-writes as YAML.

The comparison this column exists to make is a narrow one. knr-ops and chant
target the *same* runtime — Flux reconciling CAPI/CAPA and ACK custom resources
into AWS. What differs is where the structure lives: knr-ops keeps it in
directory layout and kustomize patches, chant keeps it in composites and props.

## Layout

```
chant.config.ts            lexicons, environments, ownership marker, lint config
package.json               file: deps on the two vendored chant packages
vendor/                    the vendored tarballs + why they exist
fixtures/                  recorded lifecycle snapshot + chant search/graph
                            query output for the Phase 3 comprehension tasks
                            (see fixtures/README.md); snapshot-fixtures.sh
                            regenerates it against a throwaway kind cluster
src/
  composites/              scenario-local Composite() factories
    region-cluster.ts        RegionCluster, RegionNodePool
    secure-bucket.ts         SecureBucket
    postgres-instance.ts     PostgresInstance
    reader-iam.ts            ReaderIam
    ack-controller.ts        AckController
    defaults.ts              extracted constants (EVL009 keeps them out of factories)
    labels.ts                label/tag vocabulary, derived from props
    policies.ts              IAM policy documents + other prop shapes
    secrets.ts               referenced-secret plumbing (the SOPS interim)
    index.ts                 the public surface of the composite layer
  envs/
    dev/
      infra/main.ts          dev infra build root (bucket, DB, IAM, ACK controllers)
      clusters/main.ts       dev clusters build root (RegionCluster)
      delivery/main.ts       dev delivery build root (Flux source + Kustomizations)
    prod/
      infra/main.ts          prod infra build root
      clusters/main.ts       prod clusters build root
      delivery/main.ts       prod delivery build root
```

Each environment is three build roots, not one, because `chant build <path>`
emits one output file per invocation and the delivery layer needs three
distinct targets: the Flux `Kustomization`s that reconcile `infra/` and
`clusters/` name those paths explicitly (`FluxAppFor(..., { path:
"./dist/dev/infra" })`), and a `Kustomization` cannot reconcile a path that
contains the object declaring it. See "Build output layout" below.

## Environment isolation

**Convention: two entrypoint directories, one build root each, invoking shared
composites with per-environment props.**

`src/envs/dev` and `src/envs/prod` are separate build-root trees. Each has
three sub-roots — `infra/`, `clusters/`, `delivery/` — and `chant build
src/envs/dev/infra` sees only `src/envs/dev/infra/main.ts` and the composites
it imports, never anything under `src/envs/prod` or even under
`src/envs/dev/clusters`. The two environments share every factory and share
no build artifact, no state, and no reconciliation path.

Why this and not the alternative: chant supports build-time parameters
(`buildParams` in `chant.config.ts`, bound with `chant build --param env=prod`),
which would let one entrypoint emit either environment. That is the closer
analogue to Terraform workspaces or Pulumi stack configs — and it is the wrong
choice here, for three reasons.

1. **SPEC acceptance criterion 7 asks for structural isolation.** "Changing prod
   does not modify dev state" is a property you want the file system to
   guarantee, not a flag someone has to remember to pass. A single
   parameterized entrypoint makes `chant build` with no `--param` produce *an*
   environment, and the one it produces is whichever default the config names.
2. **The environments differ in resource set, not only in sizing.** Prod has a
   replica bucket, a replication IAM role, and a `PodIdentityAssociation` that
   dev does not have; dev has an IAM user that prod deliberately does not.
   Expressing that through a parameter means conditionals in the entrypoint —
   which is where the knr-ops column's kustomize patches already live, and
   reproducing them in TypeScript would measure the wrong thing.
3. **Two trees that read alike are the demonstration.**
   `src/envs/dev/{infra,clusters,delivery}/main.ts` and their prod
   counterparts are the same shape end to end and differ only where the SPEC
   matrix says they differ. That is legible in a way a patch file is not, and
   it is what the benchmark's comprehension tasks read.

The cost is honest and worth stating: the two entrypoint trees repeat the
call structure. What they do not repeat is the resource bodies — those live
in the composites, once.

`chant.config.ts` also declares `environments: ["dev", "prod"]`. That is a
separate thing: the identities chant threads through its operational layer
(`chant lifecycle --env prod`, the component release ledger). It does not split
the build; the directories do.

The matching row is in `scenario/SPEC.md` under "Environments".

## Build output layout

`chant build <path>` writes one file per invocation — there is no flag that
splits a single build into several output files. The delivery layer needs
three, one per `FluxAppFor`/reconciliation concern, so the source tree has
three build roots per environment and `npm run build:dev`/`build:prod` invoke
`chant build` three times each:

```
dist/
  dev/
    delivery.yaml            GitRepository + both Kustomizations (bootstrap-applied directly)
    infra/manifests.yaml     bucket(s), DBInstance, IAM, ACK HelmReleases — path "./dist/dev/infra"
    clusters/manifests.yaml  RegionCluster (CAPI/CAPA + flux2 addon)  — path "./dist/dev/clusters"
  prod/
    delivery.yaml
    infra/manifests.yaml
    clusters/manifests.yaml
```

`dist/dev/infra/manifests.yaml` and `dist/dev/clusters/manifests.yaml` are
what the two `Kustomization`s declared in `src/envs/dev/delivery/main.ts`
reconcile — their `path` props (`"./dist/dev/infra"`, `"./dist/dev/clusters"`)
name exactly these locations, so `chant lint`'s `FLUX002`/`FLUX003` checks and
the files this project actually emits now agree. `dist/dev/delivery.yaml`
holds the `GitRepository` and both `Kustomization` objects themselves — the
bootstrap edge, applied directly to the management cluster the same way
flux-system's own sync manifest is, not something a `Kustomization`
reconciles (a `Kustomization` cannot reconcile the path containing the object
that declares it).

This was `golden-base/chant`'s coverage gap #8 as scaffolded in #18: the
`FluxAppFor` paths named a layout the build did not yet emit. It is resolved
here by giving each Flux path its own build root instead of changing the
paths to match a single combined file — the three-way split is what keeps
`infra` and `clusters` independently reconcilable (and independently
`dependsOn`-orderable) once this project's `dist/` is synced into the
`myapp-infra` repository `FluxGitSource` names.

## Verifying

```bash
npm install     # resolves the two vendored tarballs
npm run verify  # typecheck + chant lint + build dev/prod + kubeconform
```

Recorded output at the time of writing:

```
> tsc --noEmit -p tsconfig.json
(no output)

> chant lint src
src/composites/secure-bucket.ts
    32:1   info   Local type "SecureBucketReplication" is used in Composite prop
                  "replication" — consider using a lexicon property type instead  COR018
✓ No problems found

> chant build src/envs/dev/delivery -f yaml -o dist/dev/delivery.yaml
fold: 0 files folded, 1 ran
> chant build src/envs/dev/infra -f yaml -o dist/dev/infra/manifests.yaml
fold: 0 files folded, 1 ran
> chant build src/envs/dev/clusters -f yaml -o dist/dev/clusters/manifests.yaml
fold: 1 file folded, 0 ran
> chant build src/envs/prod/delivery -f yaml -o dist/prod/delivery.yaml
fold: 0 files folded, 1 ran
> chant build src/envs/prod/infra -f yaml -o dist/prod/infra/manifests.yaml
fold: 0 files folded, 1 ran
> chant build src/envs/prod/clusters -f yaml -o dist/prod/clusters/manifests.yaml
fold: 1 file folded, 0 ran

> kubeconform -ignore-missing-schemas -summary dist/dev/delivery.yaml dist/dev/infra/manifests.yaml dist/dev/clusters/manifests.yaml dist/prod/delivery.yaml dist/prod/infra/manifests.yaml dist/prod/clusters/manifests.yaml
Summary: 38 resources found in 6 files - Valid: 0, Invalid: 0, Errors: 0, Skipped: 38
```

Per-file document counts: `dev/delivery.yaml` 3 (GitRepository + 2
Kustomizations), `dev/infra/manifests.yaml` 9, `dev/clusters/manifests.yaml`
6 (18 total, dev); `prod/delivery.yaml` 3, `prod/infra/manifests.yaml` 11
(the replica bucket and its replication role add 2 over dev),
`prod/clusters/manifests.yaml` 6 (20 total, prod). 38 across both — unchanged
from the single-file layout; the split moves resources between files, it
does not add or drop any.

Three notes on reading that output.

- **The one `COR018` is an `info`, and it is a deliberate divergence.**
  `SecureBucketReplication` is a narrower prop than the lexicon's
  `S3Bucket_Replication` property type on purpose: a destination ARN and a
  storage class, instead of the raw ACK `{ role, roleRef, rules }` object. Using
  the lexicon type would put the whole replication rule list back at every call
  site, which is the duplication the composite exists to remove. Info-level
  rules do not fail a build and this one is answered, not suppressed.
- **`COR001` and `COR013` are switched off in `chant.config.ts`, with the
  reasons written next to them.** COR001 ("extract every inline object to an
  exported const") is good advice for a file of hand-written resources and the
  wrong advice for a composite layer, where the spec *is* built from props at
  the call site. chant's own `cockroachdb-multi-region-gke` example makes the
  same two calls for the same two reasons.
- **All 38 documents are `skipped`, not `Valid`.** Every resource this golden
  emits is a custom resource — CAPI, CAPA, CAAPH, ACK, Flux — and
  `-ignore-missing-schemas` skips what it has no schema for. Zero errors is
  therefore a real "nothing is malformed" signal but a weak one. The strong
  check runs earlier: the k8s lexicon's `WK8501`/`WK8502` post-synth rules
  validate every custom-resource spec against the shipped CRD schema during
  `chant lint` — unknown field, wrong scalar type, value outside an enum. That
  is the check kubeconform cannot make here, and it is clean.

## Lifecycle snapshot + query fixtures

`fixtures/` holds a recorded `chant lifecycle snapshot` of this estate (dev
and prod, captured off a throwaway kind cluster with the six `dist/` files
applied — no controller needs to reconcile anything; `describeResources()`
reads the objects back through the k8s API directly) plus the
`chant lifecycle show` / `chant search --explain` / `chant graph --format
ir` output the Phase 3 comprehension tasks (iac-cd-bench#22/#24/#25) seed
from, the way the knr-ops column seeds tasks from raw YAML. `fixtures/
README.md` has the full account, including two chant CLI findings from
generating it: the k8s lexicon doesn't yet reconstruct edges for `--at`/
`--live` graphs (AWS-only today), and a real `--deep` snapshot bug in the
vendored core's orphan-branch git plumbing (worked around by using identity-
depth snapshots, which don't hit it). `fixtures/snapshot-fixtures.sh`
regenerates the whole directory from scratch; it creates and deletes its own
`chant-snap` kind cluster and touches no other cluster.

## Composite prop surfaces

The four factories epic #2 asks for, plus one that earned its place. Required
props first, then optional.

### `RegionCluster` — `src/composites/region-cluster.ts`

Members: `cluster` (`K8s::CAPI::Cluster`), `infra` (`AWSManagedCluster`),
`controlPlane` (`AWSManagedControlPlane`), `nodePool` (a nested
`RegionNodePool`), `fluxAddon` (`HelmChartProxy`).

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
| `namespace` | `string` | `clusters` |
| `additionalTags` | `Record<string, string>` | — |

`RegionNodePool` is exported separately for the second-pool case: `name`,
`clusterName`, `env`, `instanceType`, `replicas`, `availabilityZones`, then
optional `minSize`, `maxSize`, `version`, `amiType`, `capacityType`,
`diskSizeGiB`, `namespace`.

An array of pools is deliberately *not* a prop. A composite factory returns a
flat record of named members, and iterating a pool array inside the factory is
exactly what `EVL010` exists to prevent. One pool per cluster is what the SPEC
asks for; a second is one more call.

### `SecureBucket` — `src/composites/secure-bucket.ts`

Members: `bucket` (`K8s::S3::Bucket`), `replicaRole` (`K8s::Iam::Role`, only
when `replication` is set).

| Prop | Type | Default |
|---|---|---|
| `name` | `string` | — |
| `env` | `string` | — |
| `region` | `string` | — |
| `kmsKeyARN` | `string` | unset → SSE-S3 (AES-256) |
| `replication` | `{ destinationBucketARN, storageClass?, prefix?, replicateExisting? }` | unset |
| `namespace` | `string` | `infra` |
| `component` | `string` | `assets` |

Versioning, encryption, public-access block, and bucket-owner-enforced
ownership are not props. There is no call that produces a bucket without them.

### `PostgresInstance` — `src/composites/postgres-instance.ts`

Members: `instance` (`K8s::Rds::DBInstance`).

| Prop | Type | Default |
|---|---|---|
| `name` | `string` | — |
| `env` | `string` | — |
| `instanceClass` | `string` | — |
| `databaseName` | `string` | — |
| `masterUsername` | `string` | — |
| `masterPassword` | `SecretRef` | — |
| `dbSubnetGroupName` | `string` | — |
| `allocatedStorageGiB` | `number` | `20` |
| `maxAllocatedStorageGiB` | `number` | unset |
| `engineVersion` | `string` | `16.4` |
| `multiAZ` | `boolean` | `false` |
| `storageEncrypted` | `boolean` | `true` |
| `kmsKeyID` | `string` | unset → AWS-managed key |
| `backupRetentionDays` | `number` | `7`; below 7 throws |
| `vpcSecurityGroupIDs` | `string[]` | unset |
| `namespace` | `string` | `infra` |
| `component` | `string` | `database` |

`deletionProtection: true` and `publiclyAccessible: false` are pinned, not
props.

### `ReaderIam` — `src/composites/reader-iam.ts`

Members: `policy` (`K8s::Iam::Policy`), `role` (`K8s::Iam::Role`), `user`
(`K8s::Iam::User`, when `programmaticAccess`), `podIdentity`
(`K8s::Eks::PodIdentityAssociation`, when `podIdentity` is set).

| Prop | Type | Default |
|---|---|---|
| `name` | `string` | — |
| `env` | `string` | — |
| `bucketName` | `string` | — |
| `trust` | `{ mode: "account", accountID, externalID? }` or `{ mode: "oidc", providerARN, issuerHost, serviceAccountNamespace, serviceAccountName }` | — |
| `prefix` | `string` | unset → whole bucket |
| `additionalActions` | `string[]` | unset |
| `programmaticAccess` | `boolean` | `false` |
| `podIdentity` | `{ clusterName, serviceAccountNamespace, serviceAccountName }` | unset |
| `maxSessionDurationSeconds` | `number` | `3600` |
| `namespace` | `string` | `infra` |
| `component` | `string` | `identity` |

`additionalActions` is a list of enumerated actions. There is no prop that
appends a wildcard, and the `oidc` trust arm pins both the service-account
`:sub` and the `:aud` — pinning only `:aud` is the IRSA over-trust that lets
every service account in the cluster assume the role.

### `AckController` — `src/composites/ack-controller.ts`

Members: `release` (`K8s::Flux::HelmRelease`).

| Prop | Type | Default |
|---|---|---|
| `service` | `string` (`s3`, `rds`, `iam`) | — |
| `env` | `string` | — |
| `region` | `string` | — |
| `chartVersion` | `string` | — |
| `repositoryName` | `string` | — |
| `interval` | `string` | `10m` |
| `dependsOn` | `{ name, namespace? }[]` | unset |
| `replicas` | `number` | `1` |
| `targetNamespace` | `string` | `ack-system` |

A fifth composite beyond the four epic #2 names. It earns its place on the same
argument: without it each environment repeats three near-identical 20-line
`HelmRelease` bodies, which is the duplication this column exists to remove.

## Secrets: the SOPS interim

Nothing secret-shaped is in this directory, and nothing secret-shaped is in the
build output. The RDS master password and the application's DB connection
string both live in Kubernetes Secrets created out of band; the `DBInstance`
points at the first by name/namespace/key, and the application consumes the
second the same way.

That is chant's **referenced provenance**: the value exists out of band, a human
or an external process put it where consumers read it, and the estate records
only that it depends on it. It is the closest of chant's three provenance kinds
(`referenced`, `from-provider`, `generated-once`) to what the knr-ops column
does with SOPS — and it is not the same thing. Committed ciphertext is a fourth
taxonomy row with no primitive yet; that gap is tracked as the SOPS-gap issue,
and it is why this column cannot mirror `golden-base/knr-ops/.sops.yaml`.

`src/composites/secrets.ts` carries a `SecretRef` type with no field that could
hold material, plus the two shapes consumers need (ACK's
`{ name, namespace, key }` and Flux's `{ name }`). When
`declareSecret({ provenance: "referenced" })` publishes, `describeSecret()`
becomes a `declareSecret()` call and every ref keeps working unchanged — see
the first coverage gap below.

## Coverage gaps

Findings, not excuses. Each is something the scenario needs that this
toolchain cannot declare today, with what was done instead.

### 1. `declareSecret` is not in the published core

The binding directive for this arm was to use
`declareSecret({ provenance: "referenced" })` for the DB secret. The primitive
is on chant's main branch (`e5ca9f63`, core #1828) and is **not** in the
published `@intentius/chant@0.46.0`, which is the newest version on the
registry. It is in the vendored core tarball, so it is technically reachable —
but a golden that depends on an unpublished API in *two* packages instead of
one is a worse artifact than one that keeps the discipline structurally. So the
provenance is enforced by construction (`SecretRef` has no material-bearing
field) and the primitive call is a one-line change when it ships.

### 2. The vendored lexicon does not work with the published core

Not a scenario gap — a toolchain one, and the reason `vendor/` has two tarballs
instead of the one the plan called for. `chant-lexicon-k8s@0.47.0`'s `wk8503`
post-synth rule imports `@intentius/chant/secret-provenance`, which
`@intentius/chant@0.46.0` does not ship. `chant lint` fails at module
resolution before reading a single source file. Vendoring the matching core
build is the only fix short of patching the lexicon.

### 3. ACK RDS coverage is `DBInstance` only

No `DBSubnetGroup` kind exists in the lexicon, so the subnet group is a
`dbSubnetGroupName` *prop* pointing at something created elsewhere rather than
a declared member. The knr-ops column declares its subnet group as a resource.
Same for `DBParameterGroup`.

### 4. ACK IAM coverage has no `AccessKey`

The SPEC's dev arm is "IAM user + programmatic access". The user is declared;
the access key is not, because `iam.services.k8s.aws` in this lexicon covers
`Role`, `User`, and `Policy` only. Policy *attachment* is fine — ACK models it
as `policyRefs` on the Role and User, so there is no `PolicyAttachment` kind to
miss.

### 5. Nothing declares the HTTPS exposure row

SPEC row 5 (internal ALB or CloudFront in dev, CloudFront + ACM in prod) and
acceptance criterion 6 (443 listener, valid cert reference) have no typed kind
in this lexicon for the cloud half: no ACK `acm`, no ACK `cloudfront`, no ACK
`elbv2`. The certificate and the CDN are not declarable here, full stop.

The in-cluster half *is* declarable — the lexicon ships a native `Ingress`
type and a pre-built `AlbIngress` composite (`certificateArn`, `scheme:
"internal" | "internet-facing"`, `sslRedirect`) that would type an ALB-backed
Ingress cleanly. It is deliberately not called here. `golden-base/knr-ops`
was checked (`infra/app/deployment.yaml`) as the directive for this arm
requires: its application `Service` is `type: ClusterIP` with no `Ingress`,
no ALB, no CloudFront, and no ACM object anywhere in that column. This arm
mirrors that scope rather than reaching past it — a golden that types more
of the HTTPS row than its comparison column declares would be measuring
lexicon coverage, not scenario parity, and the two columns exist to be
compared. Both stop at the same place: the in-cluster Service (knr-ops
declares one; this arm declares no application workload at all, since
SPEC's "Not in Scope" list excludes application code and neither the SPEC
row nor epic #2's composite list asks for one). The certificate, the load
balancer, and the CDN are the finding — on both columns — not a chant-only
gap.

### 6. Networking is referenced, never declared

VPCs, subnets, and security groups are string props (`sg-dev-database`,
`myapp-dev-subnets`). No ACK `ec2` kinds are in the lexicon. CAPA can create a
managed VPC for the cluster, which is what `AWSManagedControlPlane` does here,
but the ACK resources' networking is assumed to exist. knr-ops has the same
shape, so the comparison is fair — but neither column *declares* the network.

### 7. CRD-generated classes are typed at the top level only

`new CAPICluster({ metadata, spec })` types `spec` as
`Record<string, unknown>`. The real checking happens after synthesis, in
`WK8501`/`WK8502`, against the shipped CRD schema — which does catch a
misspelled field, a wrong scalar type, and an out-of-enum value, but catches it
at lint time rather than in the editor. The composite prop surfaces above are
where the compile-time typing actually lives, which is an argument for the
composite layer rather than against it.

Former gap 8 — delivery paths naming an output layout the build did not
emit — is resolved; see "Build output layout" above.

## Toolchain

| Piece | Version | Source |
|---|---|---|
| `@intentius/chant` | 0.49.0 (main build) | `vendor/intentius-chant-0.49.0.tgz` |
| `@intentius/chant-lexicon-k8s` | 0.49.0 (main build) | `vendor/intentius-chant-lexicon-k8s-0.49.0.tgz` |
| `kubeconform` | 0.7.0 | system |
| Node | 24.x (20.x per `mise.toml` also works) | system |

Both `file:` dependencies flip to registry versions once upstream publishes —
see `vendor/README.md`.
