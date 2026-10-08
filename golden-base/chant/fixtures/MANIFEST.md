# fixtures/MANIFEST.md

Every file in this directory is genuine `chant` CLI output, captured by
`snapshot-fixtures.sh` against a real (throwaway) kind cluster with the
golden's own `dist/` manifests applied — nothing here was hand-written or
hand-edited. This is the sidecar provenance record the fixtures README
points to: the exact command behind each file.

All commands below ran from the project root (`golden-base/chant`) unless
noted. `--src`/`--at` targets and `--env` are load-bearing — see
fixtures/README.md, "Why `--src` is required."

| File | Exact command |
|---|---|
| `snapshot-dev.json` | `git show chant/lifecycle:dev/k8s.json` (in the isolated scratch checkout, after `chant lifecycle snapshot dev --src src/envs/dev`) |
| `snapshot-prod.json` | `git show chant/lifecycle:prod/k8s.json` (after `chant lifecycle snapshot prod --src src/envs/prod`) |
| `lifecycle-show-dev.txt` | `chant lifecycle show dev` |
| `lifecycle-show-prod.txt` | `chant lifecycle show prod` |
| `search-buckets-dev.txt` | `chant search "kind:Bucket" --at latest --env dev --src src/envs/dev --explain --show labels` |
| `search-buckets-prod.txt` | `chant search "kind:Bucket" --at latest --env prod --src src/envs/prod --explain --show labels` |
| `search-prod-only-pod-identity-dev.txt` | `chant search "kind:PodIdentityAssociation" --at latest --env dev --src src/envs/dev --explain` |
| `search-prod-only-pod-identity-prod.txt` | `chant search "kind:PodIdentityAssociation" --at latest --env prod --src src/envs/prod --explain` |
| `search-iam-by-component-prod.txt` | `chant search "kind:Iam attr:labels=component\":\"identity" --at latest --env prod --src src/envs/prod --explain` |
| `search-db-secret-reference.txt` | `chant search "myapp-dev-db-master" --src src/envs/dev --explain` (declared graph, offline — no `--at`/`--live`) |
| `graph-ir-dev.json` | `chant graph src/envs/dev --format ir --at latest --env dev` |
| `graph-ir-prod.json` | `chant graph src/envs/prod --format ir --at latest --env prod` |
| `graph-ir-dev-declared.json` | `chant graph src/envs/dev --format ir` (declared graph, offline — supplementary; see README) |
| `graph-ir-prod-declared.json` | `chant graph src/envs/prod --format ir` (declared graph, offline — supplementary; see README) |

The `snapshot-*.json` and `lifecycle-show-*`/`search-*`/`graph-ir-dev.json`/
`graph-ir-prod.json` files all read from the **same pair of snapshots** —
one `chant lifecycle snapshot dev` and one `chant lifecycle snapshot prod`
run, taken in that order, against the same kind cluster, per
`snapshot-fixtures.sh`. The `*-declared.json` graphs are a separate,
offline, source-only build and are not tied to those snapshots at all.
