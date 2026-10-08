## Task: Predict delivery behavior from chant lifecycle/search/graph output

**Stack:** chant (TypeScript composites compiling to Flux + CAPI/CAPA + ACK Kubernetes manifests)

You are given a slice of a chant golden repo and a set of **genuine, recorded**
`chant` CLI query outputs taken against this estate's dev and prod
environments. Nothing in `seed/fixtures/` was hand-written — every file is
the literal stdout of the command named at its top, captured with
`chant lifecycle snapshot <env>` already run against a live (if idle)
Kubernetes cluster with this project's build output applied. Answer every
question from what these files actually say, not from what you'd expect a
chant project to look like in general.

### Repo Slice (workspace)

```
src/composites/secrets.ts             # SecretRef: referenced-provenance plumbing for the DB secret
src/composites/postgres-instance.ts   # PostgresInstance composite (uses secrets.ts)
src/envs/dev/infra/main.ts            # dev infra build root: bucket, DB, IAM, ACK controllers
src/envs/dev/clusters/main.ts         # dev clusters build root: RegionCluster
src/envs/dev/delivery/main.ts         # dev delivery build root: Flux GitRepository + 2 Kustomizations

fixtures/lifecycle-show-dev.txt                     # chant lifecycle show dev
fixtures/lifecycle-show-prod.txt                    # chant lifecycle show prod
fixtures/search-buckets-dev.txt                     # chant search "kind:Bucket" --at latest --env dev --src src/envs/dev --explain --show labels
fixtures/search-buckets-prod.txt                    # same, --env prod --src src/envs/prod
fixtures/search-prod-only-pod-identity-dev.txt      # chant search "kind:PodIdentityAssociation" --at latest --env dev --src src/envs/dev --explain
fixtures/search-prod-only-pod-identity-prod.txt     # same, --env prod --src src/envs/prod
fixtures/search-iam-by-component-prod.txt           # chant search "kind:Iam attr:labels=component\":\"identity" --at latest --env prod --src src/envs/prod --explain
fixtures/search-db-secret-reference.txt             # chant search "myapp-dev-db-master" --src src/envs/dev --explain  (declared graph, no --at/--live)
fixtures/graph-ir-dev.json                          # chant graph src/envs/dev --format ir --at latest --env dev
fixtures/graph-ir-dev-declared.json                 # chant graph src/envs/dev --format ir  (declared graph, offline)
```

You do **not** have prod's TypeScript source in this workspace — only dev's.
Everything you say about prod has to come from the recorded prod fixtures
above, cross-referenced against the composite prop tables in the provided
docs (warm condition) or your own reasoning about what the dev source
implies (cold condition).

### Questions

**Q1.** Using `lifecycle-show-dev.txt` and the three seeded
`src/envs/dev/{infra,clusters,delivery}/main.ts` files, map each of the
three build roots to the resources it contributes to the 18 recorded in
dev's snapshot. Then explain why `sourceGitRepository` and the two
`Kustomization` objects (`infraAppKustomization`, `clusterAppKustomization`)
show up in the recorded dev snapshot at all, given that `delivery/main.ts`
is not itself a path any `Kustomization` reconciles.

**Q2.** Compare `lifecycle-show-dev.txt` to `lifecycle-show-prod.txt`
(18 resources vs. 20), `search-buckets-dev.txt` to `search-buckets-prod.txt`
(1 bucket vs. 2), and `search-prod-only-pod-identity-dev.txt` to
`search-prod-only-pod-identity-prod.txt` (0 matches vs. 1). List every
resource that appears in one environment's recorded estate and not the
other's, and for each one say which composite call-site prop is
responsible for the difference.

**Q3.** `search-db-secret-reference.txt` searches for the literal string
`myapp-dev-db-master` and reports exactly one match: `databaseInstance`.
Using `postgres-instance.ts` and `secrets.ts`, explain exactly how that one
match comes to exist (which field, which function call), and why nothing
else in this golden matches. Then explain what the fixture's footer —
"declared only · no observation · physical ids unavailable" — means, and
why this particular query has that footer while the others (`search-buckets-*`,
`search-prod-only-pod-identity-*`) don't.

**Q4.** `readerUser` (`K8s::Iam::User`) appears in dev's recorded snapshot
but not prod's. `readerPodIdentity` (`K8s::Eks::PodIdentityAssociation`)
appears in prod's but dev's own search for that kind returns
`(no matches)` against the full 18-resource universe. Explain the
SPEC-driven reason for each asymmetry — they are two different props on
the same composite, not the same cause twice.

**Q5.** `graph-ir-dev.json`'s `edges` array is empty, even though
`iamControllerRelease`, `rdsControllerRelease`, and `s3ControllerRelease`
all reference `ackCharts` by name in their declared spec. Explain why the
`--at latest` graph shows zero edges here, and name the specific file in
this workspace where those same three edges *do* appear, along with how
many edges it has and why.

### Context Files

{{scenario_spec}}
