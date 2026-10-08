# fixtures/

Recorded `chant` CLI output over this golden's deployed estate — `chant
lifecycle show`, `chant search --explain`, and `chant graph --format ir` —
so the benchmark's Phase 3 comprehension tasks (T1/T5/T6, iac-cd-bench#22,
#24, #25) can seed query output the way the knr-ops column seeds raw YAML.
`MANIFEST.md` lists the exact command behind every file here.

## What "deployed estate" means for this column

chant's lifecycle model (`docs/concepts/lifecycle-models.mdx` in the chant
repo) puts truth in the live system: `chant lifecycle snapshot <env>` reads
each declared entity back through the k8s API (`describeResources()` — a
typed GET per entity, not a `kubectl` shell-out) and records the result to a
`chant/lifecycle` git orphan branch. Nothing in that path calls out to AWS —
it reads Kubernetes objects, full stop. So "deployed" here means: a real
Kubernetes API server has the golden's `dist/*.yaml` objects loaded, with
their CRDs registered so the API server accepts and serves them back.
**No controller needs to reconcile anything.** CAPI/CAPA never provision an
EKS cluster, ACK never calls AWS, Flux never fetches the git source — the
snapshot only needs the object to exist and be GET-able, which is exactly
what `kubectl apply` against a cluster with the right CRDs installed gives
you.

That is what `snapshot-fixtures.sh` does: a throwaway `kind` cluster named
`chant-snap`, the CAPI/CAPA/CAAPH/ACK/Flux CRDs the six `dist/` files
declare (fetched from the same pinned releases the k8s lexicon's
`crd-sources.ts` names — see the script), the six manifests applied with
plain `kubectl apply`, and two `chant lifecycle snapshot` runs. No AWS
account, no real EKS cluster, no running controllers anywhere. This was the
"preferred path" in the issue and it worked end to end — the lifecycle/
snapshot surface was **not** infeasible against inert applied objects, so
nothing here is a fallback or an approximation of a fixture; every file is
the same command a real operator would run against a real (if idle) estate.

## Regenerating

```bash
./fixtures/snapshot-fixtures.sh
```

Requires `docker`, `kind`, `kubectl`, `node`/`npm`, `curl`, `python3`. The
script creates the `chant-snap` kind cluster, builds `dist/`, applies CRDs
and manifests, takes both snapshots, writes every file in this directory,
rebuilds `dist/` one more time (so the working tree matches what the
snapshot actually observed), and deletes the cluster on exit (`trap cleanup
EXIT`, so it tears down even on failure). It touches no cluster other than
the one it creates and deletes.

Two details worth calling out, both because they'd silently produce wrong
fixtures if skipped:

- **Snapshot dev before applying prod.** `AckController`'s three
  HelmReleases and the shared `FluxGitSource`/`HelmRepository` are declared
  with the **same names** in both `src/envs/dev` and `src/envs/prod` (by
  design — see the golden README's "Environment isolation": the two trees
  share every factory, and on two real, separate per-environment clusters
  that would never collide). Landed on one shared kind cluster for snapshot
  purposes, applying prod second **updates those shared objects in place**
  — same UID, prod's labels. Snapshotting dev only after both applies would
  report dev's own `HelmRelease`s carrying prod's `iac-cd-bench.dev/env:
  prod` label, which is wrong. The script applies dev, snapshots dev,
  *then* applies prod, then snapshots prod — so each snapshot reads the
  estate as that environment actually left it.
- **`--src` is required on every `search`/`graph` call, not optional.**
  `chant.config.ts` here declares no `sourceDir` and no `stacks`, so a
  lifecycle command with no `--src` builds the **whole project root** —
  dev and prod combined — and reports mangled, path-prefixed logical ids
  for anything whose name collides across environments (confirmed:
  `chant search "kind:Bucket" --at latest --env dev` with no `--src` returns
  4 buckets under names like `SrcEnvsProdInfraassetsBucket`, not the 1 bucket
  dev actually declares). `--src src/envs/dev` / `--src src/envs/prod`
  scopes the build to exactly the environment being queried, matching what
  `chant lifecycle snapshot <env> --src <path>` was built from. Every
  fixture here was generated with the matching `--src`.

## Findings (chant CLI reality vs. docs)

These are toolchain observations from actually running the lifecycle/query
surface, not scenario gaps — recorded here because they shape how the
Phase 3 tasks should read these fixtures, and because #40's methodology
notes asked for exactly this kind of delta.

1. **The k8s lexicon does not implement live/replay edge reconstruction.**
   `docs/cli/graph.mdx` documents this as a real per-lexicon capability gap
   ("a lexicon without that enrichment yields nodes with fewer edges") and
   it applies to `--at latest` here in full: `graph-ir-dev.json` and
   `graph-ir-prod.json` both have real nodes (`physicalId`, `attrs`,
   `ownership`) and **zero edges** — confirmed by reading
   `describe-resources.ts` in the vendored k8s lexicon, which records
   `namespace`/`labels`/`resourceVersion`/`conditions` at identity depth and
   has no reference-catalog equivalent to what the AWS lexicon ships for
   `{Ref}`/`Fn::GetAtt` reconstruction. This is not a bug — the AWS lexicon
   is the only one with that enrichment today — but it means `--at`/`--live`
   graphs are node inventories, not relationship graphs, for this column.
   `graph-ir-dev-declared.json` / `graph-ir-prod-declared.json` are included
   as a supplementary pair: the **declared** (offline, source-only) graph,
   which does carry real edges (3 per environment — the three ACK
   `HelmRelease`s each reference the shared `HelmRepository` by name), for
   any task that wants to demonstrate `chant graph`'s edge-aware output
   without conflating it with what `--at`/`--live` actually returns for k8s
   today.
2. **`--src` is load-bearing, not cosmetic**, for the reason in
   "Regenerating" above — worth a task distractor in its own right (a
   plausible-looking `chant search ... --at latest --env dev` with no
   `--src` silently answers a different, larger question than intended).
3. **A real `chant lifecycle snapshot --deep` bug**, found and worked
   around, not present in these fixtures: `--deep` triggered
   `Bad control character in string literal in JSON` on the very next
   `chant lifecycle show`. Root cause, traced into the vendored core
   (`packages/core/src/lifecycle/git.ts`, `writeBlobToPath`): the orphan
   branch write pipes the snapshot JSON through `sh -c "echo '<content>' |
   git hash-object -w --stdin"`. `sh`'s builtin `echo` on this machine
   interprets backslash escape sequences (`\n`, `\t`, ...) rather than
   passing them through literally, so a `\n` that `JSON.stringify` correctly
   escaped inside a string value (deep observation captures
   `kubectl.kubernetes.io/last-applied-configuration`, whose value itself
   ends in a literal trailing newline byte — confirmed by reading the live
   annotation with `kubectl get -o jsonpath` — which JSON-escapes to the
   two characters `\`+`n`) comes back out of the shell pipeline as an actual
   newline byte, landing an unescaped control character inside a JSON string
   in the committed blob. Every fixture here uses **identity-depth**
   snapshots (no `--deep`), which never serializes annotations and never
   exercises this path — a real, reproducible finding, not a fixture
   defect, and not worked around by hiding it: it simply doesn't affect
   what's committed.
4. **CAAPH's `HelmChartProxy` and the ACK CRDs need no controller to
   satisfy a snapshot.** Confirmed nothing here waited on `Ready`/an
   operator: every recorded resource's `status` reads `PRESENT` (chant's
   `statusFromObject` fallback for an object with no populated `.status` at
   all), which is the expected, honest signal for an object no controller
   has touched — not a masked failure.

## Fixture inventory

- `snapshot-dev.json`, `snapshot-prod.json` — the raw `chant lifecycle
  snapshot` artifact (identity depth: type, physical id (k8s UID), status,
  `namespace`/`labels`/`resourceVersion`/`conditions` per resource), exactly
  as committed to the `chant/lifecycle` orphan branch. 18 resources for dev,
  20 for prod (prod adds the replica bucket, replication IAM role, and the
  `PodIdentityAssociation` — dev's extra is its IAM `User`, which prod
  deliberately omits in favor of OIDC pod identity; see the golden README's
  composite prop tables).
- `lifecycle-show-dev.txt`, `lifecycle-show-prod.txt` — the census view:
  every managed resource, one line each, `RESOURCE / TYPE / PHYSICAL ID /
  STATUS`. Raw terminal output, ANSI bold escapes included (chant does not
  gate color on TTY detection) — a real reality-vs-clean-fixture wrinkle
  worth knowing about if a task's grader does exact-string matching.
- `search-buckets-dev.txt` / `search-buckets-prod.txt` — "buckets by env":
  the same `kind:Bucket` query against each environment's own snapshot,
  `--show labels` so the env label is visible in the row. dev: 1 bucket.
  prod: 2 (primary + cross-region replica).
- `search-prod-only-pod-identity-{dev,prod}.txt` — a resource kind (SPEC's
  prod-only OIDC pod identity binding) present in prod's snapshot and
  entirely absent from dev's — dev's `(no matches)` result includes the
  full near-miss list `--explain` prints when nothing matches.
- `search-iam-by-component-prod.txt` — "prod-only resources" from a
  different angle: an `attr:` filter that matches prod's reader `Policy`
  and `Role` (`component=identity`) but excludes `assetsReplicaRole`, the
  cross-region-replication `Role` that exists only in prod and carries
  `component=assets` (it's `SecureBucket`'s replica role, not `ReaderIam`'s)
  — a genuine `--explain` near-miss (`2 of 3 ... matched`), not a
  contrived one.
- `search-db-secret-reference.txt` — "what references the DB secret": a
  plain substring search for the secret name over the **declared** (source)
  graph. One match — the `DBInstance` itself. This is the honest answer for
  this golden: per the README's "Secrets: the SOPS interim" section, nothing
  else in this column declares a consumer of that secret (the SPEC
  explicitly excludes application code), so a task asking "what reads the
  DB secret" should expect exactly this, not a longer chain.
- `graph-ir-{dev,prod}.json` — the snapshot-backed (`--at latest`) infra
  graph: real nodes, zero edges (finding #1 above).
- `graph-ir-{dev,prod}-declared.json` — supplementary: the same graph built
  offline from source, which does carry edges.
