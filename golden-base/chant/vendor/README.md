# vendor/

Two chant packages are pinned here as tarballs instead of resolved from npm.
**Both flip to plain registry versions once upstream publishes**, and the
`file:` entries in `../package.json` are the only lines that change.

| Tarball | Package | Why it is vendored |
|---|---|---|
| `intentius-chant-lexicon-k8s-0.49.0.tgz` | `@intentius/chant-lexicon-k8s` | Carries the CAPI/CAPA/ACK typed kinds this golden is written against (`K8s::CAPI::Cluster`, `K8s::Controlplane::AWSManagedControlPlane`, `K8s::Infrastructure::AWSManagedCluster` / `AWSManagedMachinePool`, `K8s::Addons::HelmChartProxy` / `ClusterResourceSet`, `K8s::S3::Bucket`, `K8s::Rds::DBInstance`, `K8s::Iam::Role` / `User` / `Policy`, `K8s::Eks::PodIdentityAssociation`). No published lexicon has them yet. |
| `intentius-chant-0.49.0.tgz` | `@intentius/chant` | The lexicon above imports `@intentius/chant/secret-provenance` from its `wk8503` post-synth rule, and no published core ships that module — `chant lint` dies with `Cannot find module .../src/secret-provenance.ts` before it reaches a single source file. |

## Why 0.49.0 and not 0.46.0

The previous pin (`intentius-chant-0.46.0-bench.tgz`, built from branch
`bench/lexicon-capi-ack` at `89648046`) predates two commands the benchmark
now uses:

- **`chant search`** — the edge-aware estate query. `tasks/chant/T1-comprehend`
  already seeds the model with recorded `chant search --explain` output, so the
  old pin measured a chant that could not run the command the task shows.
- **`chant scenario check`** — evaluates a declared `Scenario` (an assertion
  about what a change *does*) offline against a fixture snapshot. The fixtures
  it needs are already committed at `fixtures/snapshot-{dev,prod}.json`.

`89648046` is an ancestor of `main`, so the CAPI/CAPA/ACK lexicon work the old
pin existed for is included. Verified on bump: `chant lint` clean, `tsc
--noEmit` clean, `chant build` emits the same 38 resources, and kubeconform
validates 38/38 against the vendored schema mirror.

## Rebuilding them

From a chant checkout on `main`:

```bash
cd packages/core       && npm run build && npm pack --ignore-scripts
cd ../../lexicons/k8s  && npm pack --ignore-scripts
```

Copy the two tarballs here, then `npm install` in the parent directory.
`packages/core/dist` is tracked in the chant repo, so restore it afterwards
(`git checkout -- .`) if you build in place.

## Flipping to the registry

When both packages publish with the CAPI/CAPA/ACK kinds and the
secret-provenance module, replace the two `file:` entries in `package.json`
with version ranges, delete this directory, and re-run `npm run verify`. No
source file should need to change.
