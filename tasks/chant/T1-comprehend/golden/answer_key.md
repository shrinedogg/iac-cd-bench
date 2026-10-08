# T1-comprehend Reference Answer Key

## Scenario: predict delivery behavior from recorded chant lifecycle/search/graph output over the dev+prod estate

**Q1: Mapping the 18 dev resources to their build root, and why delivery's own objects are recorded**

`src/envs/dev/infra/main.ts` declares 9 of the 18: `assetsBucket`
(`SecureBucket`), `databaseInstance` (`PostgresInstance`), `readerPolicy` +
`readerRole` + `readerUser` (`ReaderIam` with `programmaticAccess: true`),
`ackCharts` (the shared `HelmRepository`), and `s3ControllerRelease` +
`rdsControllerRelease` + `iamControllerRelease` (three `AckController`
calls). `src/envs/dev/clusters/main.ts` declares 6: `clusterCluster`,
`clusterControlPlane`, `clusterInfra`, `clusterFluxAddon` (all from the one
`RegionCluster` call), and `clusterNodePoolAwsPool` + `clusterNodePoolMachinePool`
(`RegionCluster`'s nested `RegionNodePool`). `src/envs/dev/delivery/main.ts`
declares the remaining 3: `sourceGitRepository` (`FluxGitSource`) and
`infraAppKustomization` + `clusterAppKustomization` (two `FluxAppFor`
calls). 9 + 6 + 3 = 18, matching `lifecycle-show-dev.txt`'s header count.

The delivery objects are recorded even though no `Kustomization` reconciles
`delivery/`'s own path because `chant lifecycle snapshot dev --src
src/envs/dev` observes the **entire** dev source tree under `--src`, not
just whatever one Kustomization's `path` points at. `sourceGitRepository`
and the two `Kustomization`s are the bootstrap edge — applied directly to
the management cluster rather than reconciled by anything — but they are
still declared Kubernetes objects under `src/envs/dev`, so `describeResources()`
reads them back through the k8s API exactly like every other resource in
the tree and they appear in the recorded snapshot.

**Q2: Every dev/prod difference and its cause**

| Resource | Only in | Composite call-site cause |
|---|---|---|
| `readerUser` (`K8s::Iam::User`) | dev | dev's `ReaderIam` call sets `programmaticAccess: true`; prod's sets `false` |
| `assetsReplicaBucket` (`K8s::S3::Bucket`) | prod | prod declares a second, independent `SecureBucket` call (`assetsReplica`) that dev has no counterpart for |
| `assetsReplicaRole` (`K8s::Iam::Role`) | prod | prod's `assets` `SecureBucket` call sets the `replication` prop, which is what makes `SecureBucket` emit a `replicaRole` member; dev's `assets` call has no `replication` prop |
| `readerPodIdentity` (`K8s::Eks::PodIdentityAssociation`) | prod | prod's `ReaderIam` call sets the `podIdentity` prop; dev's call omits it entirely |

That's 18 (dev) − 1 (`readerUser`) + 3 (`assetsReplicaBucket`,
`assetsReplicaRole`, `readerPodIdentity`) = 20 (prod), matching both
`lifecycle-show-*.txt` headers. `search-buckets-dev.txt` (1 bucket) vs.
`search-buckets-prod.txt` (2) is the `assetsReplicaBucket` row from the
table above, seen through a `kind:Bucket` filter instead of the full
census. `search-prod-only-pod-identity-dev.txt`'s `(no matches)` (0 of 18)
vs. `search-prod-only-pod-identity-prod.txt`'s 1-of-1 match is the
`readerPodIdentity` row, seen the same way.

**Q3: The one DB-secret match, and the fixture's footer**

`src/envs/dev/infra/main.ts` builds a `SecretRef` named
`myapp-dev-db-master` and passes it as `masterPassword` into
`PostgresInstance({...})`. Inside `postgres-instance.ts`, that value flows
into the `DBInstance`'s spec as `masterUserPassword: secretRef(props.masterPassword)`
— `secretRef()` (from `secrets.ts`) projects the ref to `{ name, namespace,
key }`, and `name` there is literally the string `myapp-dev-db-master`.
That field, on the `databaseInstance` resource, is the only place that
string appears anywhere in this golden's declared graph — nothing else
declares a consumer of the secret (per the golden's own scope: application
code that would read the connection string is explicitly out of the SPEC's
scope), so `1 of 1 K8s::Rds::DBInstance matched` is the honest, complete
answer, not a partial one.

`search-db-secret-reference.txt`'s footer reads "declared only · no
observation · physical ids unavailable" because that command has no
`--at`/`--live` flag (per the manifest: `chant search "myapp-dev-db-master"
--src src/envs/dev --explain`, nothing else) — it ran over the **declared**
(source) graph offline, not a recorded snapshot. There is therefore no
physical id to report, unlike `search-buckets-*.txt` and
`search-prod-only-pod-identity-*.txt`, which both pass `--at latest --env
<env>` and report `observed from snapshot <commit> taken <time>` because
they read the recorded snapshot instead.

**Q4: The readerUser and PodIdentityAssociation asymmetries are two different props**

`readerUser` only in dev is the SPEC's IAM row: dev gets programmatic
access via a long-lived IAM user (`programmaticAccess: true` on dev's
`ReaderIam` call), while prod uses a least-privilege assumed role with
OIDC trust instead and deliberately has no user at all
(`programmaticAccess: false`). This is `ReaderIam`'s `user` member, which
the composite only creates `when programmaticAccess === true`.

`readerPodIdentity` only in prod is a separate SPEC row and a separate
prop: prod's `ReaderIam` call sets the `podIdentity` object (`clusterName`,
`serviceAccountNamespace`, `serviceAccountName`), binding the reader role
to a Kubernetes service account through EKS Pod Identity — the mechanism
prod's application pods actually use to assume the role. Dev's `ReaderIam`
call has no `podIdentity` prop at all, so the composite never creates that
member for dev — which is exactly what
`search-prod-only-pod-identity-dev.txt`'s `(no matches)` (0 of 18,
`PodIdentityAssociation` never in dev's universe) confirms: dev isn't
missing a resource that failed to deploy, it never declares one.

**Q5: Why the `--at latest` graph has no edges, and where the real edges are**

`graph-ir-dev.json` is the snapshot-backed graph (`chant graph src/envs/dev
--format ir --at latest --env dev`). For the k8s lexicon, `--at`/`--live`
graphs have real nodes but **zero edges** — `describeResources()` records
identity-depth attributes (`namespace`, `labels`, `resourceVersion`,
`conditions`) with no reference-catalog step to reconstruct edges from
those attributes, unlike the AWS lexicon, which does have that
enrichment. This is a documented capability gap in chant's own CLI
reference for `graph` ("a lexicon without that enrichment yields nodes
with fewer edges"), not a defect in this golden or a sign that the three
`AckController` HelmReleases don't really reference `ackCharts`.

Those three edges are exactly where `graph-ir-dev-declared.json` shows
them: `iamControllerRelease → ackCharts`, `rdsControllerRelease →
ackCharts`, and `s3ControllerRelease → ackCharts`, each `viaAttr: "spec"`
(from `spec.chart.spec.sourceRef.name`). That file is the **declared**
graph — built offline, directly from the TypeScript source, with no
`--at`/`--live` — so it resolves cross-resource references the way the
source actually wires them (`repositoryName: ackCharts.name` at every
`AckController` call site) rather than depending on live observation. It
has exactly 3 edges total, one per controller, all pointing at the one
shared `HelmRepository`.
