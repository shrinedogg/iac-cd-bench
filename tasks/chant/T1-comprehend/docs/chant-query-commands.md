# Answer estate questions with `chant search` — the recorded snapshot is the source of truth

This estate was deployed from the chant project this task's `src/` slice is
drawn from, snapshotted with `chant lifecycle snapshot <env> --src
src/envs/<env>` against a real (if idle) Kubernetes cluster — the six
`dist/` manifests applied, no controller reconciling anything. The
fixtures in this task are the recorded output of three read commands run
against that snapshot. Query the recorded output rather than re-deriving
it from the TypeScript source; the fixtures already answer what got
deployed, not just what was declared.

**`chant lifecycle show <env>`** — the complete recorded inventory: every
managed resource with its logical name, type, physical id, and status.
This is the census — read it first so you know the denominator (18 for
dev, 20 for prod) before filtering.

**`chant search "<query>" --at latest --env <env> --src <dir> [--explain] [--show a,b]`**
— filter and join over that inventory. `--src` is **load-bearing, not
cosmetic**: this project's `chant.config.ts` declares no `sourceDir`, so a
lifecycle command with no `--src` builds the whole project root — dev and
prod combined — and reports mangled, path-prefixed ids for anything whose
name collides across environments. Every fixture in this task was
generated with the matching `--src src/envs/<env>`.

**`chant graph <path> --format ir [--at latest --env <env>]`** — the whole
infra graph as JSON. `nodes` carry `id`, `kind`, `physicalId`, `attrs`.
`edges` carry `from`, `to`, `viaAttr`. Two very different graphs share this
flag shape:

- **No `--at`/`--live`** — the **declared** graph, built offline from
  source. Every cross-resource reference in your TypeScript becomes a real
  edge.
- **`--at latest --env <env>`** — the **snapshot-backed** graph, built from
  the recorded observation. For this project's lexicon (`k8s`), this graph
  has real nodes and **zero edges** — the k8s lexicon does not yet
  implement the live/replay edge-reconstruction step the AWS lexicon has
  (see `docs/cli/graph.mdx`'s documented capability gap: "a lexicon
  without that enrichment yields nodes with fewer edges"). Do not read an
  empty `edges: []` in an `--at latest` graph as evidence that a golden
  declares no relationships — check the declared graph before concluding
  that.

Every `--explain` result states what backed it: `— observed from snapshot
<commit> taken <time> · bound N/M` for a snapshot-backed query, or
`— declared only · no observation · physical ids unavailable` for a query
with no `--at`/`--live` (offline, source-only — the query ran over the
declared graph, so there is no live physical id to report). `--explain`
also adds a footer on every result: `N of M <kind> matched (query: ...)`,
plus, on a miss, the near-miss list — every candidate that was checked and
which term it failed. A `(no matches)` result is worth reading in full;
the near-miss list is the universe the query ran against, and it is often
the fastest way to confirm a resource kind genuinely isn't declared in an
environment rather than merely unobserved.

Query grammar (space-separated terms, all must match):

- `kind:<substr>` — resource kind, e.g. `kind:Bucket`, `kind:PodIdentityAssociation`
- `attr:<name>=<val>` — an attribute equals/contains a value, e.g.
  `attr:labels=component":"identity`
- a bare string with no `kind:`/`attr:` prefix — substring match over the
  resource's declared/observed identity (used by
  `search-db-secret-reference.txt` to find every entity whose spec
  mentions `myapp-dev-db-master`)

Each result row is `<logicalId>  <kind>  <physicalId>  <shown attrs>`.
`--show labels` (used by the bucket searches) prints each matched
resource's full label map, which is where the `iac-cd-bench.dev/env` value
is visible per row.

## Path to estate facts, in order

1. `chant lifecycle show <env>` — the census, when you need the
   denominator before filtering.
2. `chant search "<query>" --at latest --env <env> --src <dir> --explain`
   — the default, for any question narrower than "list everything."
3. `chant graph <path> --format ir [--at latest --env <env>]` — when the
   question is about relationships between resources rather than one
   resource's properties. Remember the declared/snapshot-backed split
   above before reading `edges`.
4. The typed source under `src/` — for intent the recorded output alone
   doesn't carry (why a prop has the value it has, not just that it does).
